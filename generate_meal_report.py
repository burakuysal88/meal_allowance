#!/usr/bin/env python3
"""
Meal Allowance Impact Report Generator
========================================

Turns a personal Spendesk expense export (CSV) into the same
"Meal Allowance Policy — Real-World Impact Analysis" PDF report:
  - currency-corrects every transaction to EUR
  - finds real business trips (consecutive days with meal expenses)
  - compares actual spend against the German statutory tax-free
    meal allowance (Verpflegungsmehraufwand), trip-by-trip
  - applies the provided-meal reduction rule (Section 9(4a) EStG)
    for likely hotel-included breakfasts
  - builds a 3-page PDF with tables and charts

HOW TO USE
----------
1. In Spendesk, export your personal expense history to CSV
   (Expenses -> filter to your own transactions -> Export).
2. Save the CSV somewhere on your computer.
3. Run:

       python generate_meal_report.py --csv "my_export.csv" --name "Jane Doe" --role "Robot Service Technician"

   The PDF will be created in the same folder as this script,
   named "Meal_Allowance_Impact_Analysis_<Name>.pdf".

REQUIREMENTS
------------
    pip install pandas matplotlib reportlab

Optional arguments:
    --csv        Path to your Spendesk CSV export (required)
    --name       Your full name, shown on the report (required)
    --role       Your job title, shown on the report (default: "Service Technician")
    --company    Company name, shown on the report (default: "Magazino GmbH")
    --output     Output PDF path (default: auto-generated in current folder)

NOTES ON METHODOLOGY (please read before sharing the report)
--------------------------------------------------------------
- Only transactions whose description contains "breakfast", "lunch",
  "dinner" or "meal" are counted. If your descriptions use different
  wording, edit the MEAL_KEYWORDS list below.
- "Local amount" in Spendesk exports can be in a foreign currency
  (e.g. NOK, CZK) even when the row looks like EUR. This script uses
  the Debit/Credit columns instead, which are already in EUR - do not
  change this without checking your own export's columns.
- The breakfast reduction assumption (hotel-included breakfast) is a
  conservative estimate based on the absence of a personally-purchased
  breakfast on non-arrival days of a multi-day trip. It cannot be
  verified from personal card data alone - see the report's own
  Methodology section for the full explanation.
- This is a personal analysis tool, not an official company or legal
  document. Figures should be double-checked before being used in any
  formal proceeding.
"""

import argparse
import os
import sys
from datetime import datetime

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                 Image, HRFlowable)
from reportlab.lib.enums import TA_CENTER

# ---------------------------------------------------------------------------
# Configuration - edit here if your Spendesk export looks different
# ---------------------------------------------------------------------------
MEAL_KEYWORDS = r'breakfast|lunch|dinner|\bmeal\b'
BREAKFAST_KEYWORD = 'breakfast'
LUNCH_KEYWORD = 'lunch'
DINNER_KEYWORD = 'dinner'

DAY_RATE_FULL = 28.0        # EStG Section 4(5): full calendar day of absence
DAY_RATE_PARTIAL = 14.0     # EStG Section 4(5): arrival/departure day, or day away >8h
BREAKFAST_DEDUCTION = 5.60  # EStG Section 9(4a): 20% of DAY_RATE_FULL
LUNCH_DEDUCTION = 11.20     # EStG Section 9(4a): 40% of DAY_RATE_FULL
DINNER_DEDUCTION = 11.20    # EStG Section 9(4a): 40% of DAY_RATE_FULL

NAVY = colors.HexColor('#1c2b4a')
GOLD = colors.HexColor('#c99a3b')
GREY = colors.HexColor('#5a6472')
LIGHT = colors.HexColor('#f4f5f7')
LOSS_RED = colors.HexColor('#8a2a1f')


# ---------------------------------------------------------------------------
# Data loading & cleaning
# ---------------------------------------------------------------------------
def load_spendesk_csv(csv_path):
    df = pd.read_csv(csv_path, sep=';', engine='python')
    required = ['Payment date', 'Description', 'Debit', 'Credit']
    missing = [c for c in required if c not in df.columns]
    if missing:
        sys.exit(
            f"ERROR: The CSV is missing expected column(s): {missing}\n"
            f"Found columns: {list(df.columns)}\n"
            f"This script expects a standard Spendesk expense export."
        )

    df['Debit'] = pd.to_numeric(df['Debit'], errors='coerce').fillna(0)
    df['Credit'] = pd.to_numeric(df['Credit'], errors='coerce').fillna(0)
    df['eur_amount'] = df['Debit'] - df['Credit']  # already EUR, refunds negative
    df['Payment date'] = pd.to_datetime(df['Payment date'], errors='coerce')
    df = df.dropna(subset=['Payment date'])
    return df


def extract_meal_transactions(df):
    desc_l = df['Description'].fillna('').str.lower()
    meal_mask = desc_l.str.contains(MEAL_KEYWORDS, regex=True) & ~desc_l.str.contains('fx fee')
    meal_df = df[meal_mask & (df['eur_amount'] > 0)].copy()
    meal_df['date_only'] = pd.to_datetime(meal_df['Payment date'].dt.date)
    d = meal_df['Description'].str.lower()
    meal_df['meal_type'] = 'other'
    meal_df.loc[d.str.contains(BREAKFAST_KEYWORD), 'meal_type'] = 'breakfast'
    meal_df.loc[d.str.contains(LUNCH_KEYWORD), 'meal_type'] = 'lunch'
    meal_df.loc[d.str.contains(DINNER_KEYWORD), 'meal_type'] = 'dinner'
    return meal_df


# ---------------------------------------------------------------------------
# Trip detection & legal allowance calculation
# ---------------------------------------------------------------------------
def build_daily_table(meal_df):
    daily = meal_df.groupby('date_only')['eur_amount'].sum().reset_index()
    daily.columns = ['date', 'spend']
    daily = daily.sort_values('date').reset_index(drop=True)

    has_breakfast = meal_df[meal_df['meal_type'] == 'breakfast'].groupby('date_only').size()
    daily['has_breakfast'] = daily['date'].isin(has_breakfast.index)

    # group into trips: consecutive calendar days (gap of 1 day = still connected)
    trip_id, trip_ids = 0, [0]
    for i in range(1, len(daily)):
        gap = (daily['date'].iloc[i] - daily['date'].iloc[i - 1]).days
        if gap > 1:
            trip_id += 1
        trip_ids.append(trip_id)
    daily['trip_id'] = trip_ids
    daily['day_index_in_trip'] = daily.groupby('trip_id').cumcount()

    trip_len_map = daily.groupby('trip_id')['date'].count()
    daily['trip_len'] = daily['trip_id'].map(trip_len_map)

    def full_day_cap(day_index, trip_len):
        if trip_len == 1:
            return DAY_RATE_PARTIAL
        if day_index == 0 or day_index == trip_len - 1:
            return DAY_RATE_PARTIAL
        return DAY_RATE_FULL

    daily['legal_cap_full'] = daily.apply(
        lambda r: full_day_cap(r['day_index_in_trip'], r['trip_len']), axis=1)

    def breakfast_reduction(r):
        # Conservative proxy: if this is not the trip's arrival day and no
        # breakfast was personally purchased, assume the hotel included it.
        if r['trip_len'] > 1 and r['day_index_in_trip'] > 0 and not r['has_breakfast']:
            return BREAKFAST_DEDUCTION
        return 0.0

    daily['breakfast_reduction'] = daily.apply(breakfast_reduction, axis=1)
    daily['legal_cap_adjusted'] = daily['legal_cap_full'] - daily['breakfast_reduction']
    return daily


def build_trip_table(daily):
    trips = daily.groupby('trip_id').agg(
        start=('date', 'min'), end=('date', 'max'), days=('date', 'count'),
        spend=('spend', 'sum'),
        legal_full=('legal_cap_full', 'sum'),
        legal_adj=('legal_cap_adjusted', 'sum'),
    ).reset_index()
    trips['over_full'] = trips['spend'] > trips['legal_full']
    trips['over_adj'] = trips['spend'] > trips['legal_adj']
    trips['gap_adj'] = trips['spend'] - trips['legal_adj']
    return trips


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------
def make_charts(by_len, trips, out_dir):
    # Chart 1: bar comparison per trip length
    fig, ax = plt.subplots(figsize=(9.2, 3.6), dpi=200)
    x = by_len['days']
    w = 0.35
    ax.bar(x - w / 2, by_len['avg_spend'], width=w, color=NAVY.hexval() if False else '#1c2b4a', label='Actual average expenditure')
    ax.bar(x + w / 2, by_len['avg_legal_adj'], width=w, color='#c99a3b', label='Legal maximum (adjusted for provided breakfast)')
    for _, row in by_len.iterrows():
        ax.text(row['days'] - w / 2, row['avg_spend'] + 2, f"€{row['avg_spend']:.0f}", ha='center', fontsize=8, color='#1c2b4a', fontweight='bold')
        ax.text(row['days'] + w / 2, row['avg_legal_adj'] + 2, f"€{row['avg_legal_adj']:.0f}", ha='center', fontsize=8, color='#8a6d1f', fontweight='bold')
        ax.text(row['days'], -14, f"n={int(row['n'])}", ha='center', fontsize=7.5, color='#8a94a3')
    ax.set_xticks(x)
    ax.set_xticklabels([f"{int(d)}-day trip" for d in x])
    ax.set_ylabel('EUR', fontsize=9)
    ax.set_title('Real trips: actual meal expenditure vs. legal maximum (adjusted for provided breakfast), by trip length',
                 fontsize=10.5, fontweight='bold', color='#1c2b4a', loc='left')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.legend(fontsize=8.5, frameon=False, loc='upper left')
    ax.grid(axis='y', linestyle='-', linewidth=0.5, color='#e3e5ea', zorder=0)
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=-max(by_len['avg_spend'].max() * 0.02, 8))
    plt.tight_layout()
    p1 = os.path.join(out_dir, 'chart_by_length.png')
    plt.savefig(p1, dpi=200, transparent=True)
    plt.close()

    # Chart 2: scatter of every trip vs its own cap
    fig, ax = plt.subplots(figsize=(9.2, 3.6), dpi=200)
    rng = np.random.default_rng(42)
    jitter = rng.uniform(-0.12, 0.12, size=len(trips))
    colors_pts = ['#c0392b' if o else '#1e7d4a' for o in trips['over_adj']]
    ax.scatter(trips['days'] + jitter, trips['spend'], c=colors_pts, s=26, alpha=0.85,
               edgecolors='white', linewidths=0.4, zorder=3)
    days_range = by_len['days'].tolist()
    legal_vals = by_len['avg_legal_adj'].tolist()
    ax.step(days_range, legal_vals, where='mid', color='#1c2b4a', linewidth=1.8, linestyle='--', zorder=2)
    ax.set_xticks(days_range)
    ax.set_xticklabels([f"{int(d)} day{'s' if d > 1 else ''}" for d in days_range])
    ax.set_xlabel('Trip length', fontsize=9)
    ax.set_ylabel('Actual meal expenditure (EUR)', fontsize=9)
    ax.set_title('Every real trip: actual expenditure vs. its own adjusted legal cap',
                 fontsize=10.5, fontweight='bold', color='#1c2b4a', loc='left')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.grid(axis='y', linestyle='-', linewidth=0.5, color='#e3e5ea', zorder=0)
    ax.set_axisbelow(True)
    n_over = int(trips['over_adj'].sum())
    n_total = len(trips)
    legend_elems = [
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#c0392b', markersize=7,
               label=f'Above adjusted legal maximum ({n_over} trips, {n_over/n_total*100:.0f}%)'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='#1e7d4a', markersize=7,
               label=f'At or below ({n_total-n_over} trips, {(n_total-n_over)/n_total*100:.0f}%)'),
        Line2D([0], [0], color='#1c2b4a', linewidth=1.8, linestyle='--', label='Avg. adjusted legal maximum'),
    ]
    ax.legend(handles=legend_elems, fontsize=8, frameon=False, loc='upper left')
    plt.tight_layout()
    p2 = os.path.join(out_dir, 'chart_scatter.png')
    plt.savefig(p2, dpi=200, transparent=True)
    plt.close()

    return p1, p2


# ---------------------------------------------------------------------------
# PDF building
# ---------------------------------------------------------------------------
def stat_block(number, label, width, styles):
    numstyle = ParagraphStyle('statnum2', parent=styles['statnum'], fontSize=16)
    t = Table([[Paragraph(number, numstyle)],
               [Paragraph(label, styles['statlabel'])]], colWidths=[width])
    t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#e3e5ea')),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT),
        ('TOPPADDING', (0, 0), (-1, 0), 8), ('BOTTOMPADDING', (0, 0), (-1, 0), 2),
        ('TOPPADDING', (0, 1), (-1, 1), 0), ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))
    return t


def build_pdf(output_path, name, role, company, date_range_str,
              n_meal_tx, n_total_tx, n_days, n_days_breakfast,
              yearly_df, mealtype_df, by_len, trips,
              avg_spend_day, avg_legal_full_day, avg_legal_adj_day,
              n_over_full, n_over_adj, n_trips,
              total_gap_full, total_gap_adj,
              avg_loss_day, avg_loss_trip, annual_loss, annual_trips, annual_days,
              chart1_path, chart2_path):

    styles = {
        'title': ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=19, textColor=NAVY, leading=22),
        'subtitle': ParagraphStyle('subtitle', fontName='Helvetica', fontSize=10.5, textColor=GREY, leading=14),
        'h2': ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=12.5, textColor=NAVY, spaceBefore=14, spaceAfter=6),
        'body': ParagraphStyle('body', fontName='Helvetica', fontSize=9.5, textColor=colors.HexColor('#2b2f36'), leading=14),
        'small': ParagraphStyle('small', fontName='Helvetica', fontSize=8, textColor=GREY, leading=11),
        'statnum': ParagraphStyle('statnum', fontName='Helvetica-Bold', fontSize=20, textColor=NAVY, alignment=TA_CENTER, leading=22),
        'statlabel': ParagraphStyle('statlabel', fontName='Helvetica', fontSize=8, textColor=GREY, alignment=TA_CENTER, leading=10),
    }

    doc = SimpleDocTemplate(output_path, pagesize=A4, topMargin=16 * mm, bottomMargin=14 * mm,
                             leftMargin=16 * mm, rightMargin=16 * mm)
    story = []
    W = 174 * mm  # safe full content width

    story.append(Paragraph('Meal Allowance Policy — Real-World Impact Analysis', styles['title']))
    story.append(Paragraph(f'{name} · {role}, {company} · Prepared from Spendesk expense history ({date_range_str})', styles['subtitle']))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width='100%', thickness=1.2, color=GOLD))
    story.append(Spacer(1, 10))

    story.append(Paragraph(
        f'This analysis is based on {n_meal_tx} actual meal-related expense transactions (breakfast, lunch, dinner) '
        f'recorded in Spendesk over the period {date_range_str}, currency-corrected to EUR. It is intended to '
        f'ground the discussion about the meal allowance policy in real spending data rather than estimates.',
        styles['body']))
    story.append(Spacer(1, 10))

    col_w = 43.5 * mm
    pct_over_adj = n_over_adj / n_trips * 100
    stats = Table([[
        stat_block(f'{n_trips}', 'Distinct real trips identified<br/>' + date_range_str, col_w, styles),
        stat_block(f'€{avg_spend_day:.2f}', 'Average actual meal<br/>expenditure per travel day', col_w, styles),
        stat_block(f'€{avg_legal_adj_day:.2f}', 'Average legal allowance/day<br/>(adjusted for provided meals)', col_w, styles),
        stat_block(f'{pct_over_adj:.0f}%', 'of trips where meal expenditure<br/>exceeded the legal maximum', col_w, styles),
    ]], colWidths=[col_w] * 4, hAlign='LEFT')
    stats.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                                ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(stats)
    story.append(Spacer(1, 14))

    callout_table = Table([[Paragraph(
        f'<b>Key finding:</b> Each of the {n_trips} real trips was measured against the exact legal tax-free '
        f'maximum for its length, correctly reduced wherever a meal (breakfast) was likely already provided free '
        f'of charge — per the statutory Kürzung rule (Section 9(4a) of the German Income Tax Act (EStG)). Even '
        f'after this reduction, on <b>{n_over_adj} of {n_trips} trips ({pct_over_adj:.0f}%)</b>, actual meal '
        f'expenditure still exceeded the allowance. Total excess over the (adjusted) legal maximum: '
        f'<b>€{total_gap_adj:,.2f}</b> over the full period.',
        ParagraphStyle('callout', fontName='Helvetica', fontSize=9.7, textColor=colors.white, leading=13))]],
        colWidths=[W])
    callout_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(callout_table)
    story.append(Spacer(1, 8))

    # Financial Impact box
    fi_header = ParagraphStyle('fi_header', fontName='Helvetica-Bold', fontSize=10.5, textColor=NAVY, leading=13)
    fi_num = ParagraphStyle('fi_num', fontName='Helvetica-Bold', fontSize=15, textColor=LOSS_RED, alignment=TA_CENTER, leading=18)
    fi_label = ParagraphStyle('fi_label', fontName='Helvetica', fontSize=8, textColor=GREY, alignment=TA_CENTER, leading=10)
    fi_col_w = W / 3
    fi_cells = Table([[
        Table([[Paragraph(f'€{avg_loss_day:.2f}', fi_num)], [Paragraph('Average out-of-pocket loss<br/>per travel day', fi_label)]], colWidths=[fi_col_w]),
        Table([[Paragraph(f'€{avg_loss_trip:.2f}', fi_num)], [Paragraph('Average out-of-pocket loss<br/>per business trip', fi_label)]], colWidths=[fi_col_w]),
        Table([[Paragraph(f'~€{annual_loss:,.0f}', fi_num)], [Paragraph('Estimated annual out-of-pocket<br/>cost under the new policy', fi_label)]], colWidths=[fi_col_w]),
    ]], colWidths=[fi_col_w] * 3)
    fi_cells.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    fi_box = Table([
        [Paragraph('Financial Impact', fi_header)],
        [fi_cells],
        [Paragraph(
            f'Based on {n_trips} real trips ({date_range_str}), extrapolated at the observed rate of '
            f'~{annual_trips:.0f} trips / ~{annual_days:.0f} travel days per year. This is the amount this '
            f'employee would have had to absorb personally, had the new tax-free-only reimbursement cap already '
            f'applied throughout this period.', styles['small'])],
    ], colWidths=[W])
    fi_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fbf3ec')),
        ('BOX', (0, 0), (-1, -1), 1, GOLD),
        ('TOPPADDING', (0, 0), (-1, 0), 8), ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('TOPPADDING', (0, 1), (-1, 1), 4), ('BOTTOMPADDING', (0, 1), (-1, 1), 6),
        ('TOPPADDING', (0, 2), (-1, 2), 2), ('BOTTOMPADDING', (0, 2), (-1, 2), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    story.append(fi_box)
    story.append(Spacer(1, 4))

    story.append(Paragraph('Methodology', styles['h2']))
    story.append(Paragraph(
        'A "trip" is defined as a run of consecutive calendar days containing at least one recorded meal expense '
        '(breakfast/lunch/dinner). Each trip is compared with the legal maximum applicable to its duration — a '
        '2-day trip against the 2-day maximum, a 5-day trip against the 5-day maximum, and so on. The German '
        'tax-free meal allowance (Verpflegungsmehraufwand, Section 4(5) EStG, unchanged since 2021) is €14 for '
        'the arrival/departure day of a multi-day trip (or any single day away &gt;8 hours), and €28 per full '
        'calendar day of absence in between.', styles['body']))
    story.append(Spacer(1, 8))

    story.append(Paragraph('Accounting for the provided-meal reduction rule (Section 9(4a) of the German Income Tax Act (EStG))', styles['h2']))
    story.append(Paragraph(
        'German tax law requires the allowance to be reduced when a meal is provided free of charge during the '
        'trip: <b>-€5.60 (20%)</b> if breakfast is provided, <b>-€11.20 (40%)</b> each if lunch or dinner is '
        'provided — always calculated against the €28 full-day rate, even on a €14 day. This has been factored '
        'in as follows:', styles['body']))
    story.append(Spacer(1, 3))
    pct_bf = n_days_breakfast / n_days * 100 if n_days else 0
    story.append(Paragraph(
        f'<b>Breakfast:</b> only {n_days_breakfast} of {n_days} travel days ({pct_bf:.0f}%) show a '
        f'personally-purchased breakfast. To avoid overstating the gap, the analysis conservatively assumes that '
        f'whenever no breakfast purchase appears on a non-arrival hotel day, breakfast was included by the hotel '
        f'and the statutory deduction has therefore been applied — consistent with standard practice at German '
        f'business hotels. This is why the adjusted average legal allowance (€{avg_legal_adj_day:.2f}/day) is '
        f'lower than the unadjusted statutory rate (€{avg_legal_full_day:.2f}/day).', styles['body']))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        '<b>Lunch/dinner:</b> no equivalent reduction could be applied. Every lunch/dinner counted in "actual '
        'meal expenditure" is, by definition, a personal purchase — direct proof that the meal was not provided '
        'free of charge. Any separate instance of company-paid group catering (e.g. a workshop lunch) would never '
        'appear as a personal Spendesk transaction at all, so it cannot be detected or adjusted for from this '
        'data. If such days exist, the true gap would be slightly smaller than shown — but there is no way to '
        'identify or quantify them, so no adjustment has been made here. This is flagged as an open limitation '
        'rather than estimated.', styles['body']))
    story.append(Spacer(1, 4))

    pct_over_full = n_over_full / n_trips * 100
    scen_data = [
        ['', 'Unadjusted (full statutory rate)', 'Adjusted (breakfast reduction applied)'],
        ['Avg. legal allowance / travel day', f'€{avg_legal_full_day:.2f}', f'€{avg_legal_adj_day:.2f}'],
        ['Trips exceeding the legal maximum', f'{n_over_full} / {n_trips} ({pct_over_full:.0f}%)', f'{n_over_adj} / {n_trips} ({pct_over_adj:.0f}%)'],
        ['Total excess over legal maximum', f'€{total_gap_full:,.2f}', f'€{total_gap_adj:,.2f}'],
    ]
    scen_tab = Table(scen_data, colWidths=[60 * mm, 57 * mm, 57 * mm], hAlign='LEFT')
    scen_tab.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dcdfe4')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(scen_tab)
    story.append(Spacer(1, 10))

    def simple_table(header, rows, widths):
        data = [header] + rows
        t = Table(data, colWidths=widths, hAlign='LEFT')
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dcdfe4')),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        return t

    story.append(Paragraph('Spending by year', styles['h2']))
    yrows = [[str(int(r['Year'])), f"€{r['sum']:.2f}", str(int(r['count'])), f"€{r['mean']:.2f}"] for _, r in yearly_df.iterrows()]
    story.append(simple_table(['Year', 'Total spend', 'Transactions', 'Avg / transaction'], yrows, [30 * mm, 40 * mm, 40 * mm, 45 * mm]))
    story.append(Spacer(1, 12))

    story.append(Paragraph('Spending by meal type', styles['h2']))
    mrows = [[r['type'], str(int(r['count'])), f"€{r['total']:.2f}", f"€{r['avg']:.2f}"] for _, r in mealtype_df.iterrows()]
    story.append(simple_table(['Meal', 'Transactions', 'Total', 'Avg / meal'], mrows, [30 * mm, 40 * mm, 40 * mm, 45 * mm]))
    story.append(Spacer(1, 12))

    story.append(Paragraph('Spending by trip length (adjusted for provided breakfast)', styles['h2']))
    brows = []
    for _, r in by_len.iterrows():
        gap = r['avg_spend'] - r['avg_legal_adj']
        brows.append([f"{int(r['days'])} day{'s' if r['days'] > 1 else ''}", str(int(r['n'])),
                      f"€{r['avg_spend']:.2f}", f"€{r['avg_legal_adj']:.2f}", f"€{gap:+.2f}"])
    story.append(simple_table(['Trip length', 'Trips', 'Avg actual expenditure', 'Legal max (adj.)', 'Avg gap'],
                               brows, [32 * mm, 22 * mm, 38 * mm, 33 * mm, 30 * mm]))
    story.append(Spacer(1, 12))

    story.append(Paragraph('Actual expenditure vs. adjusted legal maximum, by trip length', styles['h2']))
    story.append(Image(chart1_path, width=W, height=W * 3.6 / 9.2))
    story.append(Spacer(1, 6))

    story.append(Paragraph('Every individual trip, compared to its own adjusted legal cap', styles['h2']))
    story.append(Image(chart2_path, width=W, height=W * 3.6 / 9.2))
    story.append(Spacer(1, 10))

    story.append(Paragraph('Implications', styles['h2']))
    story.append(Paragraph(
        f'This analysis does not indicate excessive spending by {name.split()[0] if name else "the employee"}. '
        'Rather, it reflects the unavoidable cost of purchasing meals while travelling for work and staying in '
        f'hotels without access to cooking facilities. Even after conservatively applying all legally required '
        f'breakfast deductions, actual meal expenditure exceeded the adjusted tax-free allowance on '
        f'{pct_over_adj:.0f}% of real business trips. The issue is therefore not the existence of the statutory '
        'allowance itself, but that reducing company support to only the statutory tax-free amount may not '
        'reflect the travel reality of employees who regularly spend several nights each week away from home, '
        'unlike colleagues whose work mainly consists of day trips.', styles['body']))
    story.append(Spacer(1, 12))

    story.append(HRFlowable(width='100%', thickness=0.75, color=colors.HexColor('#dcdfe4')))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f'Data source: personal Spendesk expense export, {n_total_tx} total transactions, {n_meal_tx} identified '
        'as meal-related (breakfast/lunch/dinner), currency-corrected to EUR using actual charged amounts. Legal '
        'maximum figures apply Section 4(5) EStG rates and are reduced per Section 9(4a) EStG on days where '
        'breakfast was likely provided (inferred from the absence of a personally-purchased breakfast on '
        'non-arrival days of a trip). Lunch/dinner reductions could not be estimated from personal card data — '
        'see Methodology. This report was generated with a shared internal script; figures are only as accurate '
        'as the underlying Spendesk export and should be reviewed before use in any formal discussion.',
        styles['small']))

    doc.build(story)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip().strip('"').strip("'")
    return val if val else default


def run():
    interactive_mode = not bool(sys.argv[1:])  # no CLI args at all -> was interactive
    parser = argparse.ArgumentParser(description='Generate a Meal Allowance Impact Analysis PDF from a Spendesk export.')
    parser.add_argument('--csv', default=None, help='Path to your Spendesk CSV export')
    parser.add_argument('--name', default=None, help='Your full name (shown on the report)')
    parser.add_argument('--role', default=None, help='Your job title')
    parser.add_argument('--company', default=None, help='Company name')
    parser.add_argument('--output', default=None, help='Output PDF path (default: auto-generated)')
    args = parser.parse_args()

    # Interactive mode: if no --csv was given on the command line, ask for
    # everything step by step instead. This is the path non-technical
    # colleagues should use (just double-click run_report.bat / .command).
    if not args.csv:
        print("=" * 60)
        print(" Meal Allowance Impact Report - interactive setup")
        print("=" * 60)
        print("Answer the questions below. Press Enter to accept a")
        print("suggested value shown in [brackets].\n")
        args.csv = ask("Path to your Spendesk CSV export (drag & drop the file here)")
        while not args.csv or not os.path.exists(args.csv):
            print(f"  Could not find that file: {args.csv!r}")
            args.csv = ask("Please enter the path to your Spendesk CSV export again")
        args.name = ask("Your full name")
        while not args.name:
            args.name = ask("Your full name (required)")
        args.role = ask("Your job title", default='Service Technician')
        args.company = ask("Company name", default='Magazino GmbH')
        print()

    if not args.csv or not os.path.exists(args.csv):
        sys.exit(f"ERROR: file not found: {args.csv}")
    if not args.name:
        sys.exit("ERROR: --name is required")
    args.role = args.role or 'Service Technician'
    args.company = args.company or 'Magazino GmbH'

    work_dir = os.path.dirname(os.path.abspath(args.output)) if args.output else os.getcwd()
    os.makedirs(work_dir, exist_ok=True)

    print("Loading CSV...")
    df = load_spendesk_csv(args.csv)
    n_total_tx = len(df)

    print("Identifying meal transactions...")
    meal_df = extract_meal_transactions(df)
    n_meal_tx = len(meal_df)
    if n_meal_tx == 0:
        sys.exit("ERROR: no meal-related transactions found (breakfast/lunch/dinner keywords in Description). "
                  "Check MEAL_KEYWORDS at the top of this script if your descriptions use different wording.")

    date_min = meal_df['Payment date'].min()
    date_max = meal_df['Payment date'].max()
    date_range_str = f"{date_min.strftime('%b %Y')} - {date_max.strftime('%b %Y')}"
    years = max((date_max - date_min).days / 365.25, 1 / 365.25)

    print("Building daily & trip tables...")
    daily = build_daily_table(meal_df)
    trips = build_trip_table(daily)

    n_days = len(daily)
    n_days_breakfast = int(daily['has_breakfast'].sum())
    n_trips = len(trips)
    n_over_full = int(trips['over_full'].sum())
    n_over_adj = int(trips['over_adj'].sum())

    total_spend = daily['spend'].sum()
    total_legal_full = daily['legal_cap_full'].sum()
    total_legal_adj = daily['legal_cap_adjusted'].sum()
    total_gap_full = total_spend - total_legal_full
    total_gap_adj = total_spend - total_legal_adj

    avg_spend_day = total_spend / n_days
    avg_legal_full_day = total_legal_full / n_days
    avg_legal_adj_day = total_legal_adj / n_days

    avg_loss_day = total_gap_adj / n_days
    avg_loss_trip = trips['gap_adj'].mean()
    annual_loss = total_gap_adj / years
    annual_trips = n_trips / years
    annual_days = n_days / years

    yearly_df = meal_df.groupby(meal_df['Payment date'].dt.year)['eur_amount'].agg(['sum', 'count', 'mean']).reset_index()
    yearly_df.columns = ['Year', 'sum', 'count', 'mean']

    rows = []
    for kw, label in [(BREAKFAST_KEYWORD, 'Breakfast'), (LUNCH_KEYWORD, 'Lunch'), (DINNER_KEYWORD, 'Dinner')]:
        sub = meal_df[meal_df['meal_type'] == kw]
        rows.append({'type': label, 'count': len(sub), 'total': round(sub['eur_amount'].sum(), 2),
                     'avg': round(sub['eur_amount'].mean(), 2) if len(sub) else 0})
    mealtype_df = pd.DataFrame(rows)

    by_len = trips.groupby('days').agg(n=('trip_id', 'count'), avg_spend=('spend', 'mean'),
                                        avg_legal_full=('legal_full', 'mean'), avg_legal_adj=('legal_adj', 'mean')).reset_index()

    print("Building charts...")
    chart1, chart2 = make_charts(by_len, trips, work_dir)

    if args.output:
        output_path = args.output
    else:
        safe_name = args.name.strip().replace(' ', '_')
        output_path = os.path.join(work_dir, f'Meal_Allowance_Impact_Analysis_{safe_name}.pdf')

    print("Building PDF...")
    build_pdf(
        output_path, args.name, args.role, args.company, date_range_str,
        n_meal_tx, n_total_tx, n_days, n_days_breakfast,
        yearly_df, mealtype_df, by_len, trips,
        avg_spend_day, avg_legal_full_day, avg_legal_adj_day,
        n_over_full, n_over_adj, n_trips,
        total_gap_full, total_gap_adj,
        avg_loss_day, avg_loss_trip, annual_loss, annual_trips, annual_days,
        chart1, chart2,
    )

    print(f"\nDone! Report saved to: {output_path}")

    if interactive_mode:
        input("\nPress Enter to close this window...")


def main():
    interactive_mode = not bool(sys.argv[1:])
    try:
        run()
    except SystemExit as e:
        # our own sys.exit(...) calls with a friendly message
        if e.code and not isinstance(e.code, int):
            print(str(e.code))
        if interactive_mode:
            input("\nSomething went wrong (see message above). Press Enter to close...")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        if interactive_mode:
            input("\nSomething went wrong. Press Enter to close...")
        sys.exit(1)


if __name__ == '__main__':
    main()
