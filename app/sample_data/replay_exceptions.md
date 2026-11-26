# Historical regression exceptions

The canonical records are in
[demo-data/replay_exceptions.json](../../demo-data/replay_exceptions.json), with
[their evidence and cohort notes](../../demo-data/replay_exceptions.md).
The obsolete app-local copy has been removed so runtime checks and the demo use
the same inspected outcomes. There are 121 records; 120 apply to the full-history
observed-prior replay. The remaining record describes a pending-batch state effect.

Runtime checks pin actual status/code, predicted status/full code set and any
recorded remark. Pending-batch demo checks additionally require the recorded
period end. Neither accepts execution errors or automatically records exceptions.
Unlabelled historical rows contribute provisional forecasts but are excluded from
outcome agreement. Changed forecasts still appear during rule-update approval.
