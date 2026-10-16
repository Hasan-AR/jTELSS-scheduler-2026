import json
import os
import tempfile
import pandas as pd
from flask import Flask, render_template, request, send_file, after_this_request
from scheduling import LP_SCHEDULER
from file_transformer import FileTransformer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

scheduler = LP_SCHEDULER()
file_transformer = FileTransformer()
app = Flask(__name__, template_folder=TEMPLATES_DIR)

def process_schedule(file, num_slots):
    """
    For each day (1 to 6 corresponding to Monday–Saturday),
    create as many slots as indicated in the num_slots dictionary.
    Each slot gets three sessions.
    """
    time_slots_per_day = [num_slots[x] for x in num_slots.keys()]
    print('time_slots_per_day', time_slots_per_day)
    data = pd.read_excel(file, sheet_name='For program chairs', header=1)
    structured_data = file_transformer.generate_structured_file(data)
    workshop_count = len(structured_data.index)
    schedule_json = scheduler.schedule_sessions(structured_data, time_slots_per_day)
    if isinstance(schedule_json, dict):
        schedule_json["workshop_count"] = workshop_count
    return schedule_json

@app.route("/scheduling-assistant", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Check which file input was used.
        if "excel_file" in request.files:
            # Generate a new schedule from accepted workshops.
            file = request.files.get("excel_file")
            # Read one number for each day (Monday=1, …, Saturday=6)
            num_slots = {
                1: int(request.form.get("slots_monday", 0)),
                2: int(request.form.get("slots_tuesday", 0)),
                3: int(request.form.get("slots_wednesday", 0)),
                4: int(request.form.get("slots_thursday", 0)),
                5: int(request.form.get("slots_friday", 0)),
                6: int(request.form.get("slots_saturday", 0))
            }
            schedule_json = process_schedule(file, num_slots)
            return render_template("schedule.html", schedule=schedule_json)
    return render_template("index.html")

# New route for uploading an existing (exported) schedule
@app.route("/upload-schedule", methods=["POST"])
def upload_schedule():
    """
    Receives an Excel file (exported schedule) and converts it back to the schedule JSON.
    The Excel file is expected to have been exported by our export function, i.e. it has:
      - A sheet named "Schedule"
      - A MultiIndex on the columns where the first level is day names (Monday, …, Saturday)
        and the second level is session labels (e.g. "Session 1", "Session 2", "Session 3").
      - An index of time slots (starting at 1).
    """
    file = request.files.get("schedule_file")
    if not file:
        return "No schedule file uploaded", 400

    # Read the Excel file using a two-row header to reconstruct the MultiIndex columns.
    try:
        df = pd.read_excel(file, sheet_name="Schedule", header=[0, 1], index_col=0)
    except Exception:
        df = pd.read_excel(file, sheet_name="Schedule", header=0, index_col=0)
    # Assume the index is the time slot number.
    max_slots = df.shape[0]

    # Mapping day number to day name.
    day_names = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}

    # Create a reverse mapping from day name to day number.
    day_numbers = {v: k for k, v in day_names.items()}

    # Build the schedule JSON.
    schedule_json = {"days": []}
    workshop_titles = set()
    # We assume that the exported Excel file contains columns ordered as in export:
    # For each day (Monday to Saturday) there are three columns in order.
    for day in range(1, 7):
        day_name = day_names[day]
        day_entry = {"day": day, "slots": []}
        # For each time slot (row) in the DataFrame:
        for slot_idx in range(max_slots):
            slot_entry = {"slot": slot_idx + 1, "sessions": []}
            # For each session column for the day (assumed to be Session 1 to Session 3)
            for s in range(1, 4):
                col = (day_name, f"Session {s}")
                # If the column exists (it should) and the cell is not empty:
                if col in df.columns:
                    value = df.at[slot_idx + 1, col]  # since the index starts at 1
                    if pd.notna(value) and isinstance(value, str) and value.strip() != "":
                        # Expecting the cell value format to be: "title (first_author)"
                        if "(" in value and value.endswith(")"):
                            title, rest = value.split("(", 1)
                            first_author = rest.rstrip(")")
                            title = title.strip()
                            first_author = first_author.strip()
                        else:
                            title = value.strip()
                            first_author = ""
                        # Reconstruct a session object. (Defaults for response_id, session_id, workshop_type.)
                        session = {
                            "response_id": 0,
                            "session_id": 0,
                            "first_author": first_author,
                            "title": title,
                            "workshop_type": "Workshop"
                        }
                        slot_entry["sessions"].append(session)
                        if title:
                            workshop_titles.add(title)
            day_entry["slots"].append(slot_entry)
        schedule_json["days"].append(day_entry)

    schedule_json["workshop_count"] = len(workshop_titles)

    # Optionally, write the JSON to a temp file for debugging (serverless-safe).
    try:
        temp_json_path = os.path.join(tempfile.gettempdir(), "conference_schedule_uploaded.json")
        with open(temp_json_path, "w") as json_file:
            json.dump(schedule_json, json_file, indent=4)
    except OSError:
        pass

    return render_template("schedule.html", schedule=schedule_json)


@app.route("/export", methods=["POST"])
def export():
    """
    Receives the updated schedule JSON from the client and creates an Excel file where:
      - The columns are a MultiIndex: the top level is the day name (Monday to Saturday) and
        the second level is the session number within that time slot.
      - Each row corresponds to a time slot.
      - Each session is written in its own cell.
    """
    if not request.is_json:
        return "Invalid request: expected JSON body.", 400
    payload = request.get_json(silent=True) or {}
    schedule = payload.get("schedule")
    if not schedule or "days" not in schedule:
        return "Invalid request: missing schedule data.", 400
    # now = datetime.datetime.now()
    # timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")

    # Mapping day number to day name.
    day_names = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}

    # Create a mapping from day number to its schedule data.
    dayMap = {day["day"]: day for day in schedule["days"]}

    # Compute the maximum number of time slots across all days.
    max_slots = max((len(day["slots"]) for day in schedule["days"]), default=0)

    # We will have three columns per day.
    # Build a list of column tuples: (Day, Session Label)
    columns = []
    for d in range(1, 7):
        for s in range(1, 4):
            columns.append((day_names[d], f"Session {s}"))

    # Prepare a dictionary that will hold a list (of length max_slots) for each column.
    data = {col: [] for col in columns}

    # For each time slot index (row), fill in the cells for each day and each session.
    for i in range(max_slots):
        for d in range(1, 7):
            # Get the slots for this day; if the day is missing, use an empty list.
            day_obj = dayMap.get(d, {"slots": []})
            slots = day_obj.get("slots", [])
            for s in range(3):
                col = (day_names[d], f"Session {s+1}")
                if i < len(slots):
                    slot = slots[i]
                    sessions = slot.get("sessions", [])
                    if s < len(sessions):
                        sess = sessions[s]
                        value = f"{sess['title']} ({sess['first_author']})"
                    else:
                        value = ""
                else:
                    value = ""
                data[col].append(value)

    # Create a MultiIndex for the columns.
    columns_index = pd.MultiIndex.from_tuples(list(data.keys()))

    # Build the DataFrame.
    # The index will be the time slot number (starting at 1).
    df = pd.DataFrame(data, index=range(1, max_slots + 1))
    df.index.name = "Time Slot"
    df = df[list(data.keys())]  # Reorder columns in the order of our keys.
    df.columns = columns_index

    try:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        temp_path = temp_file.name
        temp_file.close()

        with pd.ExcelWriter(temp_path, engine="xlsxwriter") as writer:
            df.to_excel(writer, sheet_name="Schedule")

        @after_this_request
        def cleanup(response):
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return response

        return send_file(
            temp_path,
            as_attachment=True,
            download_name="schedule.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as exc:
        return f"Export failed: {exc}", 500

if __name__ == "__main__":
    app.run(debug=False) #, port=4010)
