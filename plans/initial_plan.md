## The opportunity: getting claims right the first time
Exchange4Health sees AI as a key driver of future growth. After a recent internal workshop, the leadership team has
made claims its top priority.
The idea. Build an AI assistant for agencies' billing teams. It would read each payer's rules, check a claim against them
before submission, predict which claims will be denied, and suggest the fix.
Who benefits. Providers, who today deal with different rules from every payer and lose time and cash to rejected
claims.
Data available. Claims history with outcomes (paid, denied, reason), plus payer rule documents and contracts.
7 The ask

Leadership has framed this as an assistant. You're free to be more ambitious if you think the case supports it. Using
Claude Code, we'd like you to:
1. Frame the problem. Decide which part of claims you'll tackle, who it's for and what good looks like. Keep the
scope to something you can build well in the time.
2. Design the system. Sketch the main components, what the AI does versus what's rules-based, what data and
context it needs, and where a human stays in the loop.
3. Build it. A working prototype that runs on the synthetic data provided.
4. Check it works. Show how you'd know whether it's right, for example by testing it against the claims history.

## Core Goal:
User: Home care agency finance person. They oversee caregivers and need payment from payers for their claimed hours. 

Take a claim they may wish to submit, along with claims history, EVV records, authorisations, and payer rules.

Output predicted denial codes, denial reasons, suggested actions.

Potentially: Automate suggested fixes to a claim. 

The goal is for agencies to be able to submit claims which are as correct as possible, with as little manual effort as possible. 

### Extra Requirements:
- Should be able to adapt to rules changes easily. 
- Should be easy for a human to reach in and fix any missed issues
    - This should be easily possible - the proposed assistant just suggests if a claim is legitimate or not, so a human is naturally in the loop. Also, the payer is still left to approve the claim. 
- Avoid using too many credits

### Definition of done

Prototype app which can be demoed to potential users (agency finance people who are currently writing and submitting claims), to get suggested improvements. 

Output checked on historical data, with accuracy number. Try and give failures to a human to review rather than making an error. 

## Plan:

Overall plan: implement a fully rules-based system, evaluate where AI can plug in the gaps. 

- Rules based system taking claims and outputting denial codes. 
    - How to maintain flexibility with rules? Could do a first pass to check the system is up to date. 
    - Can try and build a modular system, which can be easily updated with extra passes to rules, with included "rule update" functionality.

Questions to answer:
- How resilient is a rules based system, supposing the rules given are fixed?
    - Build a system which simply implements the rules as written. Test on datapoints in claims history - make sure dates match so we don't accidentally check claims which are outdated. What percent is legitimate?
    - What cases fall through? Can they be deterministically fixed, or is it a case for AI?

From these conclusions, think about what approach could find the best interventions.  
Try plugging gaps with AI. Assess on the prior datapoints in claims history.

Build a demo app with  the final model as a backend. 


## Future ideas (out of scope):

Suppose we wanted to automate claim generation - this should be rules based and possible based on the evv system, assuming these are submitted instantaneously, perhaps the next day (so if someone starts work close to midnight the previous day they are sure to be done by the processing date). 
Input: Full EVV data for a day. 
Output: Claims

It seems unfair for a claim to be denied - will a caregiver then not be paid for hours worked? If we are able to build a reliable system, perhaps this should be integrated into the EVV system where a worker should be notified if their work is violating the rules and the payment might not go through. 
Presumably caregivers work in shifts - they should never be assigned shifts that aren't valid.
Input: Authorisations, prior EVV.
Output: Validation for shifts, or notifications to workers who are about to do too much overtime.

This tool is proposed for agencies. The payer presumably must then check it also. It might be sensible to ask the payers to instead encode their rules in some regular format, then implement a shared deterministic system, to remove this guesswork. 

Use results to update rules - eg, allow the user to input a csv of results and analyse this compared to what the current rules propse, to update the rules intelligently. It's unclear how useful this is, as surely a much simpler method would be for the payer to simply communicate the rules they are applying. 