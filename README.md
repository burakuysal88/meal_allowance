# Meal Allowance Impact Report — How to generate your own

This tool turns **your own** Spendesk expense history into the same
"Meal Allowance Policy — Real-World Impact Analysis" report, using
**your** trips and **your** numbers. Nothing is shared between
colleagues — everyone runs it on their own laptop, with their own data.

## 🚀 Quick start (no coding, no command line)

**1) Get the files.**
On the GitHub page, click the green **"Code"** button → **"Download ZIP"**.
Unzip it anywhere (e.g. your Desktop).

**2) Install Python** (skip if you already have it).
Go to [python.org/downloads](https://www.python.org/downloads/), download
the installer, run it. **On the first screen, tick the box that says
"Add Python to PATH"** before clicking Install — this step is important.

**3) Export your data from Spendesk.**
Spendesk → **Expenses** → filter to your own transactions → **Export** → **CSV**.
Save it somewhere you can find it (e.g. Desktop).

**4) Double-click the launcher for your system:**
- Windows: `run_report_windows.bat`
- Mac: `run_report_mac.command`

A black window will open and ask you a few simple questions:
- the path to your Spendesk CSV (you can literally **drag the file into
  the window** and it will fill in the path for you)
- your name
- your job title (press Enter to use the suggestion)
- your company name (press Enter to use the suggestion)

**5) Done.** A file named `Meal_Allowance_Impact_Analysis_YourName.pdf`
will appear in the same folder. Open it like any PDF.

If anything goes wrong, the window will show a message and stay open
(it won't just disappear) — take a screenshot of the message and ask
for help.

---

## Advanced: command-line usage

If you're comfortable with a terminal, you can skip the questions and
run everything in one line instead:

```
python generate_meal_report.py --csv "my_export.csv" --name "Your Name" --role "Your Job Title"
```

Replace:
- `my_export.csv` with the actual path/filename of the CSV you exported
- `"Your Name"` with your full name
- `"Your Job Title"` with your role (e.g. "Robot Service Technician")

Example:

```
python generate_meal_report.py --csv "C:\Users\jsmith\Desktop\spendesk_export.csv" --name "John Smith" --role "Robot Service Technician"
```

If `python` isn't recognized, try `python3` instead.

## Optional settings

```
--company "Magazino GmbH"     # change the company name shown on the report
--output "custom_path.pdf"    # choose a specific output location/filename
```

## Troubleshooting

**"ERROR: The CSV is missing expected column(s)"**
Your export doesn't match the expected Spendesk format. Make sure you
exported from the *Expenses* view (not Cards, Invoices, or another view).

**"ERROR: no meal-related transactions found"**
The script looks for the words "breakfast", "lunch", "dinner" or "meal"
in your transaction descriptions. If you usually write something else
(e.g. always in German), open `generate_meal_report.py` in a text editor,
find the line near the top that says:

```python
MEAL_KEYWORDS = r'breakfast|lunch|dinner|\bmeal\b'
```

and add your own words, e.g.:

```python
MEAL_KEYWORDS = r'breakfast|lunch|dinner|\bmeal\b|frühstück|mittagessen|abendessen'
```

**Numbers look off / a trip is split into two / a day is missing**
The tool only sees a "travel day" if there is at least one meal expense
recorded that day. If you sometimes forget to log a meal, or a company
card was used instead of Spendesk for a specific meal, that day may be
undercounted. This is a known limitation — see the report's own
"Methodology" section for full details.

## A note on the methodology

This report:
- Currency-corrects every transaction to EUR (Spendesk sometimes shows a
  foreign amount in a column that looks like EUR — this is handled
  automatically using the Debit/Credit columns).
- Groups your expenses into real trips (consecutive days with a meal
  expense), and compares **each trip only against the legal tax-free
  maximum for its own length** — not a fixed weekly assumption.
- Applies the statutory reduction for likely hotel-included breakfast
  (Section 9(4a) EStG), conservatively estimated from your own purchase
  pattern.
- Cannot detect company-paid group catering (e.g. a workshop lunch),
  since those never appear as a personal Spendesk transaction. This is
  flagged in the report as an open limitation.

This is a personal analysis tool, not an official company or legal
document — please review your own report before using it in any formal
discussion.
