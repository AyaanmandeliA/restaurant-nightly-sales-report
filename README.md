# Restaurant nightly sales report

Scheduled jobs that pull the day's restaurant orders from the Limetray analytics API and post a sales summary to a WhatsApp group.

## What it does

- `functions/nightly_report`: for a brand with several outlets, sends two WhatsApp messages each night. The first has the day's total, the month-to-date total, each branch broken down by order channel (POS and delivery aggregators), and totals per channel. The second is a month-to-date view by branch and channel.
- `functions/split_report`: for a brand with one dine-in outlet plus cloud kitchens, sends the same two-message format with the dine-in outlet and the cloud kitchens shown as separate sections.
- `functions/hourly_pulse`: for a single outlet, sends a "today so far" message by channel. It is meant to run hourly.

## How it works

Each folder is a Google Cloud Function (HTTP trigger) started by Cloud Scheduler.

1. Build a request for the report date (midnight to 23:59:59, UAE time, UTC+4) and the configured outlet IDs.
2. Fetch paid orders from the Limetray analytics API. Requests retry a few times on failure.
3. Group revenue and order counts by outlet and by order source. Known sources are mapped to fixed channel names; others fall into "Other".
4. Build month-to-date figures. The multi-outlet report fetches one day at a time, because single whole-month queries timed out at higher order volumes. The split report fetches the month in one query.
5. Send the formatted text through the Green API WhatsApp gateway. If there are no orders the send is skipped.

Outlet IDs, outlet names and brand names are read from a JSON config (`REPORT_CONFIG` or `REPORT_CONFIG_FILE`). Credentials and endpoints are read from environment variables listed in `.env.example`.

`config.example.json` is synthetic: the IDs and names in it are made up.

## Stack

Python 3, Google Cloud Functions, Cloud Scheduler, `functions-framework`, `requests`, Limetray analytics API, Green API (WhatsApp).

## Status

Live, internal.

## Removed for privacy

This is a cleaned snapshot without the original history. Access tokens, the WhatsApp gateway instance and group identifiers, cloud project details, real outlet IDs and outlet names, sales figures, logs and internal notes were removed. Hard-coded outlet lists were moved into a config file, and the brand names in message headers were made configurable.

I set the requirements and check the results. AI tools write most of the code.
