"""Execute proposed Python rule functions in a bounded, isolated worker.

The supported language is Python expressions/control flow over JSON data, with
selected collection methods and date helpers. No imports, I/O or introspection.
The worker has a memory limit, per-call instruction budget and host wall timeout.
This is a local single-user demo, not a general-purpose hostile-code sandbox.
"""
import ast
import atexit
import json
from pathlib import Path
import re
import selectors
import subprocess
import sys
import threading
from functools import lru_cache


class RuleCodeError(ValueError):
    pass


METHODS = {"get", "items", "keys", "values", "copy", "append", "extend", "pop", "remove", "update",
           "setdefault", "count", "index", "startswith", "endswith", "split", "strip", "lower", "upper", "join"}
BUILTINS = {k: v for k, v in vars(__import__('builtins')).items() if k in {
    "int", "float", "str", "bool", "dict", "list", "set", "tuple", "len", "min", "max", "sum", "abs",
    "round", "sorted", "any", "all", "enumerate", "zip", "range", "isinstance", "ValueError"}}
BANNED = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.AsyncFunctionDef, ast.Await,
          ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal, ast.Delete, ast.Yield, ast.YieldFrom)


def validate_code(source):
    if not isinstance(source, str) or not source.strip() or len(source) > 30000:
        raise RuleCodeError("Rule code must be nonempty Python source (at most 30,000 characters)")
    return _validate_code(source)


@lru_cache(maxsize=64)
def _validate_code(source):
    try:
        tree = ast.parse(source)
    except (RecursionError, MemoryError):
        raise RuleCodeError("Rule code is too complex to parse") from None
    except SyntaxError as exc:
        raise RuleCodeError(f"Rule code syntax error on line {exc.lineno}: {exc.msg}") from None
    if not tree.body or any(not isinstance(n, ast.FunctionDef) for n in tree.body):
        raise RuleCodeError("Rule code must contain function definitions only")
    entry = [n for n in tree.body if n.name == "apply_rule"]
    if len(entry) != 1 or [a.arg for a in entry[0].args.args] != ["claim", "context", "failures"]:
        raise RuleCodeError("Define apply_rule(claim, context, failures)")
    for node in ast.walk(tree):
        if isinstance(node, BANNED):
            raise RuleCodeError(f"{type(node).__name__} is unavailable in rule code")
        if isinstance(node, ast.Name) and node.id.startswith('_'):
            raise RuleCodeError("Private names and introspection are unavailable")
        if isinstance(node, ast.Attribute) and node.attr not in METHODS:
            raise RuleCodeError(f"Method {node.attr!r} is unavailable; use JSON data and the supplied date helpers")
        if isinstance(node, ast.FunctionDef) and (node.name.startswith('_') or node.decorator_list
                                                or node.returns or node.args.defaults or node.args.kw_defaults):
            raise RuleCodeError("Use plain functions without decorators, defaults, or annotations")
        if isinstance(node, ast.arg) and (node.annotation or node.arg.startswith('_')):
            raise RuleCodeError("Use plain argument names without annotations")
        if isinstance(node, ast.Call) and not isinstance(node.func, (ast.Name, ast.Attribute)):
            raise RuleCodeError("Call named helpers or supported collection methods")
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Pow, ast.LShift, ast.RShift)):
            raise RuleCodeError("Power and bit-shift operations are unavailable")
    return source


def validate_failures(value):
    if not isinstance(value, list) or len(value) > 100:
        raise RuleCodeError("apply_rule must return a list of at most 100 failures")
    seen = set()
    for failure in value:
        if not isinstance(failure, dict) or not re.fullmatch(r"D\d{2}", str(failure.get('code', ''))):
            raise RuleCodeError("Each failure needs a denial code such as D10")
        if failure['code'] in seen:
            raise RuleCodeError("Return each denial code once")
        seen.add(failure['code'])
        if any(not isinstance(failure.get(k), str) or not failure[k].strip() or len(failure[k]) > 4000
               for k in ('meaning', 'reason', 'suggestion')):
            raise RuleCodeError("Each failure needs nonempty meaning, reason and suggestion strings")
    return sorted([{k: f[k] for k in ("code", "meaning", "reason", "suggestion")} for f in value], key=lambda f: f["code"])


_lock = threading.Lock()
_worker = None


def close_worker():
    global _worker
    if _worker:
        _worker.kill()
        _worker.wait()
        _worker.stdin.close()
        _worker.stdout.close()
        _worker = None


atexit.register(close_worker)


def apply_code(source, claim, context, failures):
    """Only JSON copies cross the process boundary; never pass live app objects."""
    global _worker
    validate_code(source)
    data = json.dumps(dict(source=source, claim=claim, context=context, failures=failures))
    if len(data) > 2_000_000:
        raise RuleCodeError("Rule execution input exceeds the 2 MB limit")
    with _lock:
        if _worker is None or _worker.poll() is not None:
            close_worker()
            _worker = subprocess.Popen([sys.executable, '-I', str(Path(__file__).resolve()), '--worker'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, bufsize=1, cwd='/tmp', env={'PYTHONIOENCODING': 'utf-8'})
        completed = False
        try:
            _worker.stdin.write(data + '\n')
            _worker.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(_worker.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=3):
                    raise RuleCodeError("Rule execution timed out")
            line = _worker.stdout.readline()
            if not line:
                raise RuleCodeError("Rule worker stopped (execution or memory limit)")
            result = json.loads(line)
            completed = True
            if 'error' in result:
                raise RuleCodeError(result['error'])
            return validate_failures(result['failures'])
        except (BrokenPipeError, OSError, ValueError) as exc:
            if not completed:
                close_worker()
            if isinstance(exc, RuleCodeError):
                raise
            raise RuleCodeError(f"Rule execution failed: {exc}") from None


def _serve():
    import resource
    from datetime import date
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    code_cache = {}
    for line in sys.stdin:
        try:
            data = json.loads(line)
            source = validate_code(data['source'])
            if source not in code_cache:
                if len(code_cache) >= 32:
                    code_cache.clear()
                code_cache[source] = compile(source, '<payer-rule>', 'exec')
            scope = {'__builtins__': BUILTINS,
                     'days_between': lambda start, end: (date.fromisoformat(end) - date.fromisoformat(start)).days,
                     'weekday': lambda day: date.fromisoformat(day).weekday()}
            budget = [20000]
            def trace(frame, event, arg):
                if frame.f_code.co_filename == '<payer-rule>':
                    budget[0] -= 1
                    if budget[0] <= 0:
                        raise RuleCodeError('Rule instruction budget exceeded')
                return trace
            sys.settrace(trace)
            try:
                exec(code_cache[source], scope)
                result = scope['apply_rule'](data['claim'], data['context'], data['failures'])
            finally:
                sys.settrace(None)
            reply = {'failures': validate_failures(result)}
        except BaseException as exc:
            reply = {'error': f'{type(exc).__name__}: {str(exc)[:500]}'}
        print(json.dumps(reply), flush=True)


if __name__ == '__main__' and '--worker' in sys.argv:
    _serve()
