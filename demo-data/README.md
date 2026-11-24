# Historical claim checking demo

Start **Historical demo** in the app: the initial date is **2026-04-30**.

1. Fast forward to **2026-06-30**. This updates the system's reference data.
2. Open **Check claims** and upload `claim-checking-csvs/claims_2026-06-01_to_2026-06-30.csv` (361 claims).
3. Inspect predictions and the **DEMO - COMPARING WITH TRUE HISTORICAL RESULTS** card.
4. Fast forward to July 31 and upload July's CSV. Review/upload the Northwind July bulletin in Payer rules and re-upload the CSV to see the effect.
5. Repeat for August 31 and the Harbor August bulletin, then subsequent month ends through January 31, 2027.

CSV filenames describe inclusive **submission date** ranges. The CSVs contain claim input fields only, with recorded statuses and denial codes removed. Use the file picker in Check claims to load them. Advancing clears the previous batch without running a check or accepting rule updates.

`reference-data/` holds the source reference CSVs, including recorded outcomes for comparison. `payer-updates/` contains the supplied manuals and bulletins. `replay_exceptions.*` documents known differences for the original three-stage CLI replay; monthly batches may differ because pending claims within each batch consume balances according to predictions.

The system date limits available reference data. For prediction, history is further restricted to submissions before the first date in the uploaded batch, preventing its recorded outcomes from entering the checker. Historical comparison uses the original recorded outcomes after prediction. Submission dates proxy outcome availability because adjudication dates were not supplied. Recorded outcomes are synthetic demonstration data, not evidence of production accuracy.
