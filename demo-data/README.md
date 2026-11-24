# Historical claim checking demo

Start **Historical demo** in the app: the initial date is **2026-04-30**.

1. Fast forward to **2026-06-30**. June is the pending batch: authorizations and EVV visits are available through June 30, but claim outcome history contains only submissions before June 1. June's claims are absent from that history.
2. Open **Check claims** and upload `claim-checking-csvs/claims_2026-06-01_to_2026-06-30.csv` (361 claims).
3. Inspect predictions and the **DEMO - COMPARING WITH TRUE HISTORICAL RESULTS** card.
4. Fast forward to July 31. June's recorded outcomes now enter history, while July's remain held out. Upload July's CSV. Review/upload the Northwind July bulletin in Payer rules and re-upload the CSV to see the effect.
5. Repeat for August 31 and the Harbor August bulletin, then subsequent month ends through January 31, 2027.

CSV filenames describe inclusive **submission date** ranges. The CSVs contain claim input fields only, with recorded statuses and denial codes removed. Use the file picker in Check claims to load them. Advancing clears the previous batch without running a check or accepting rule updates.

`reference-data/` holds the source reference CSVs, including recorded outcomes for comparison. `payer-updates/` contains the supplied manuals and bulletins. `replay_exceptions.*` documents the seven unexplained June D99 denials and known differences for the original three-stage CLI replay; monthly batches may differ because pending claims within each batch consume balances according to predictions.

This is a monthly pending-batch simulation: at each month end, that month’s claims are still awaiting submission/checking. Fast forward adds the previous months’ recorded outcomes to reference history and holds out the selected month. The system date limits authorizations and visits. For prediction, history is further restricted to submissions before the first date in the uploaded batch, preventing its recorded outcomes from entering the checker. Historical comparison uses the original recorded outcomes after prediction. Submission dates proxy outcome availability because adjudication dates were not supplied. Recorded outcomes are synthetic demonstration data, not evidence of production accuracy.

The header labels **Today’s date (simulated)**. Reference data shows its availability date, the start of each available record range, and the earlier claim history cutoff actually used for an uploaded batch. Documented differences remain prediction errors and stay in the agreement denominator.
