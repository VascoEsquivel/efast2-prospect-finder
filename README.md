# DOL EFAST2 Self-Funded Employer Finder

A Streamlit app that searches the DOL EFAST2 / Form 5500 dataset for employers with self-funded or level-funded health plans and predicts self-funded likelihood from plan name and participant count, anchored on the 150+ employee threshold. Designed as a prioritization aid for Apollo/Bombora outreach lists — results include a Priority Tier, Self-Funded Likelihood label, and CSV/text exports ready to paste into a sales sequence.

## How to run locally

```bash
pip install -r requirements.txt
streamlit run funding_lookup.py
```

Then open `http://localhost:8501` in your browser.

If the EFAST2 search API is unreachable from your network, the app falls back to a CSV uploader — download a Form 5500 dataset from the [DOL site](https://www.dol.gov/agencies/ebsa/employers-and-advisers/plan-administration-and-compliance/reporting-and-filing/form-5500/5500-datasets) and upload it for local filtering.

## Also in this repo

`fantasy_draft/` holds a separate Streamlit app: a fantasy basketball draft assistant for the 2026-27 NBA season. See [fantasy_draft/README.md](fantasy_draft/README.md). For Streamlit Community Cloud, it's published on its own branch, `fantasy-draft-app`, with the app at the top level.
