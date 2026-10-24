"""Write the tracker tabs to Google Sheets, keeping anything your team typed in.

Authentication: a Google Cloud service account. Put its JSON key in the
GOOGLE_SERVICE_ACCOUNT_JSON environment variable (or a file path in
GOOGLE_APPLICATION_CREDENTIALS) and share the spreadsheet with the service
account's email as an Editor.
"""

import csv
import json
import logging
import os

log = logging.getLogger(__name__)

STATUS_COLORS = {
    "Cancelled": {"red": 0.96, "green": 0.80, "blue": 0.80},
    "Postponed": {"red": 1.0, "green": 0.95, "blue": 0.70},
    "Notice only": {"red": 0.85, "green": 0.92, "blue": 1.0},
    "Sold": {"red": 0.88, "green": 0.88, "blue": 0.88},
    "Held": {"red": 0.88, "green": 0.88, "blue": 0.88},
}


def _client():
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not path:
            raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON (or GOOGLE_APPLICATION_CREDENTIALS) to write to Sheets")
        creds = Credentials.from_service_account_file(path, scopes=scopes)
    return gspread.authorize(creds)


class SheetWriter:
    def __init__(self, spreadsheet_id):
        self.book = _client().open_by_key(spreadsheet_id)

    def _tab(self, title, cols):
        import gspread

        try:
            return self.book.worksheet(title), False
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title=title, rows=200, cols=max(cols, 10))
            return ws, True

    def read_rows(self, title):
        """Existing rows as dicts (header -> value), or [] if the tab doesn't exist yet."""
        import gspread

        try:
            values = self.book.worksheet(title).get_all_values()
        except gspread.WorksheetNotFound:
            return []
        if not values:
            return []
        header = values[0]
        return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in values[1:]]

    def write_table(self, title, header, rows, status_col=None):
        ws, created = self._tab(title, len(header))
        values = [header] + [[_cell(r.get(h, "")) for h in header] for r in rows]
        ws.clear()
        ws.resize(rows=max(len(values) + 20, 50), cols=len(header))
        ws.update(values=values, range_name="A1", value_input_option="USER_ENTERED")
        if created:
            self._format_new_tab(ws, header, status_col)

    def append_rows(self, title, header, rows):
        ws, created = self._tab(title, len(header))
        if created or not ws.row_values(1):
            ws.update(values=[header], range_name="A1")
            self._format_new_tab(ws, header, None)
        if rows:
            ws.append_rows([[_cell(r.get(h, "")) for h in header] for r in rows],
                           value_input_option="USER_ENTERED")

    def _format_new_tab(self, ws, header, status_col):
        requests = [
            {"updateSheetProperties": {
                "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"}},
            {"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                               "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85}}},
                "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        ]
        if status_col and status_col in header:
            letter = _col_letter(header.index(status_col))
            for status, color in STATUS_COLORS.items():
                requests.append({"addConditionalFormatRule": {"index": 0, "rule": {
                    "ranges": [{"sheetId": ws.id, "startRowIndex": 1}],
                    "booleanRule": {
                        "condition": {"type": "CUSTOM_FORMULA",
                                      "values": [{"userEnteredValue": f'=LEFT(${letter}2,{len(status)})="{status}"'}]},
                        "format": {"backgroundColor": color}}}}})
        self.book.batch_update({"requests": requests})


class CsvWriter:
    """Offline stand-in for SheetWriter: writes one CSV per tab into a folder."""

    def __init__(self, folder):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)

    def _path(self, title):
        return os.path.join(self.folder, title.replace(" ", "_").replace("/", "-") + ".csv")

    def read_rows(self, title):
        if not os.path.exists(self._path(title)):
            return []
        with open(self._path(title), newline="") as f:
            return list(csv.DictReader(f))

    def write_table(self, title, header, rows, status_col=None):
        with open(self._path(title), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({h: _cell(r.get(h, "")) for h in header})

    def append_rows(self, title, header, rows):
        exists = os.path.exists(self._path(title))
        with open(self._path(title), "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            if not exists:
                w.writeheader()
            for r in rows:
                w.writerow({h: _cell(r.get(h, "")) for h in header})


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s
