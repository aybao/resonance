## What I would do next:

- Work with users to refine system requirements, add any extra features, and add integration into the real systems they are currently using.
- Refine documentation
- Build into a proper system (connect to real database, etc.)
- Build proper test suite - integration, E2E and unit tests, and decide when each run (eg. on CI)
- Build a proper sandbox around the AI generated code, to protect against a malicious user.


## Alternative ideas (out of scope):

I focused in on the small part of the workflow the task suggested we implement. Alternative ideas might be better, but depend on context and data we weren't given so I decided to avoid that.

- Automated claims generation. 
    - This should be rules based, likely based on the EVV system, assuming these are submitted instantaneously, perhaps the next day (so if someone starts work close to midnight the previous day they are sure to be done by the processing date). 
Input: Full EVV data for a day, authorisations, etc.
Output: Claims

This can then automatically only output legitimate claims, unless someone worked hours that weren't authorised.

- Work shift scheduler
    - It seems unfair for a claim to be denied - will a caregiver then not be paid for hours worked? If we are able to build a reliable system, perhaps this should be integrated into the EVV system where a worker should be notified if their work is violating the rules and the payment might not go through. 
    - Presumably caregivers work in shifts - they should never be assigned shifts that aren't valid.
Input: Authorisations, prior EVV.
Output: Validation for shifts, or notifications to workers who are about to do too much overtime.

- Authorisation request automation
    - If we start with a source of truth for which patients need what work, we should be able to automatically generate what authorisations are needed, then automatically generate shifts, then automatically generate claims.
    - This should be able to avoid errors almost entirely, with humans only needing to take care of unusual situations (EVV verification denied etc.)


Extending outside the agencies:
    -  The payer presumably must then check the claims also. It might be sensible to ask the payers to instead encode their rules in some regular format, then implement a shared deterministic system, to remove this guesswork. 

Extend the rule updater:
- eg. Use results to update rules.
    - allow the user to input a csv of returned results and analyse this compared to what the current rules propose, to update the rules intelligently. It's unclear how useful this is, as surely a much simpler method would be for the payer to simply communicate the rules they are applying. 