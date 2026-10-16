import json
import os
import tempfile
import pandas as pd
from pulp import LpProblem, LpVariable, LpBinary, lpSum, LpMinimize

class LP_SCHEDULER:
    def __init__(self):
        self.input_json_file_path = ''
        self.output_schedule_json_path = ''

    def _normalize_value(self, value):
        normalized = str(value).replace('&#43;', '+')
        return ' '.join(normalized.strip().lower().split())

    def _parse_duration_slots(self, value):
        normalized = self._normalize_value(value)
        allowed = {
            '90 minutes (standard)': 1,
            '180 minutes (cannot be guaranteed)': 2,
            'half-day, extra program day saturday, 23 may': 2,
            '90 minutes + 90 minutes: on different days (cannot be guaranteed)': 2
        }
        return allowed.get(normalized)

    def _parse_schedule_type(self, value):
        normalized = self._normalize_value(value)
        allowed = {
            'main program monday-friday (all workshops are either 90 or 180 minutes)': 'main',
            'extra program day saturday 23 may (half-day or full-day workshop options)': 'extra'
        }
        return allowed.get(normalized)

    def _coauthor_list(self, value):
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        normalized = ' '.join(str(value).split())
        if not normalized:
            return []
        return [name.strip() for name in normalized.split(',') if name.strip()]

    def read_sessions_from_excel(self, file_path):
        sessions_df = pd.read_excel(file_path)
        return sessions_df


    def schedule_sessions(self, structured_data, time_slots_per_day):
        # sessions_df = self.read_sessions_from_excel(file_path)
        sessions_df = structured_data.copy()
        if 'Session ID' not in sessions_df.columns:
            sessions_df['Session ID'] = sessions_df.index.astype(str)
        sessions_df['Session ID'] = sessions_df['Session ID'].astype(str)
        if sessions_df['Session ID'].duplicated().any():
            sessions_df['Session ID'] = sessions_df['Session ID'] + '_' + sessions_df.index.astype(str)
        sessions = sessions_df.set_index('Session ID').to_dict('index')
        session_ids = list(sessions.keys())


        total_days = len(time_slots_per_day)
        total_slots = sum(time_slots_per_day)
        time_slot_ids = list(range(total_slots))

        day_slot_mapping = []
        current_slot = 0
        for day, slots in enumerate(time_slots_per_day):
            for slot in range(slots):
                day_slot_mapping.append((day, slot))
                current_slot += 1

        def solve_model(enforce_author_constraint, enforce_coauthor_constraint):
            x = LpVariable.dicts("x", [(s, t) for s in session_ids for t in time_slot_ids], cat=LpBinary)
            prob = LpProblem("Event_Scheduling", LpMinimize)

            # Each session must be scheduled for a number of slots equal to its duration.
            for session_id, session in sessions.items():
                duration_value = session.get('Preferred duration in the main program.', '')
                duration_slots = self._parse_duration_slots(duration_value)
                if not duration_slots:
                    print(f"Warning: invalid duration '{duration_value}' for session {session_id}. Defaulting to 1 slot.")
                    duration_slots = 1
                prob += lpSum([x[session_id, t] for t in time_slot_ids]) == duration_slots

            # At most 3 sessions can be scheduled in any given time slot.
            for time_slot in time_slot_ids:
                prob += lpSum([x[session_id, time_slot] for session_id in session_ids]) <= 3

            # Sessions assigned to the extra program day must be scheduled on the last day.
            for session_id, session in sessions.items():
                schedule_value = session.get('Scheduling', '')
                schedule_type = self._parse_schedule_type(schedule_value)
                if schedule_type == 'extra':
                    for time_slot in time_slot_ids:
                        day, slot = day_slot_mapping[time_slot]
                        if day != total_days - 1:  # last day has index total_days - 1
                            prob += x[session_id, time_slot] == 0
                elif schedule_type == 'main':
                    for time_slot in time_slot_ids:
                        day, slot = day_slot_mapping[time_slot]
                        if day == total_days - 1:
                            prob += x[session_id, time_slot] == 0
                else:
                    print(f"Warning: invalid scheduling value '{schedule_value}' for session {session_id}.")

            if enforce_author_constraint:
                for time_slot in time_slot_ids:
                    attr_values = sessions_df['Full name of the first author'].unique()
                    for value in attr_values:
                        prob += lpSum([x[session_id, time_slot] for session_id, session in sessions.items() if
                                       session['Full name of the first author'] == value]) <= 1

            if enforce_coauthor_constraint:
                all_coauthors = set()
                for session in sessions.values():
                    for name in self._coauthor_list(session.get('Co-authors')):
                        all_coauthors.add(name)
                for time_slot in time_slot_ids:
                    for name in all_coauthors:
                        prob += lpSum([
                            x[session_id, time_slot]
                            for session_id, session in sessions.items()
                            if name in self._coauthor_list(session.get('Co-authors'))
                        ]) <= 1

            # Set a zero objective so the solver focuses on feasibility.
            prob += 0
            prob.solve()
            return prob, x

        prob, x = solve_model(enforce_author_constraint=True, enforce_coauthor_constraint=True)
        if prob.status != 1:
            prob, x = solve_model(enforce_author_constraint=False, enforce_coauthor_constraint=False)

        # schedule = {day: {slot: [] for slot in range(time_slots_per_day[day])} for day in range(1, total_days)}
        schedule = {day: {slot: [] for slot in range(time_slots_per_day[day])} for day in range(total_days)}
        for session_id in session_ids:
            for time_slot in time_slot_ids:
                if x[session_id, time_slot].value() == 1:
                    day, slot = day_slot_mapping[time_slot]
                    if day in schedule and slot in schedule[day]:
                        schedule[day][slot].append(session_id)

        # for day in range(1, total_days): #(total_days):
        #     print(f"Day {day + 1}:")
        #     for slot, sessions in schedule[day].items():
        #         print(f"  Slot {slot + 1}: {sessions}")

        if prob.status != 1:
            print(f"Solver status: {prob.status}. No feasible solution found.")
            return {"days": []}

        schedule_json = {
            "days": []
        }

        for day in range(total_days): #(total_days):
            day_entry = {"day": day + 1, "slots": []}
            for slot, session_ids in schedule[day].items():
                slot_entry = {"slot": slot + 1, "sessions": []}
                for session_id in session_ids:
                    if session_id in sessions:  # Ensure session_id exists in sessions dictionary
                        session = sessions[session_id]
                        # slot_entry["sessions"].append({
                        #     "Response ID": session_id,
                        #     "Seed": session_id,
                        #     "Full name of the first author": session.get("Full name of the first author", "Unknown"),
                        #     "Title": session.get("Workshop title", "Unknown"),
                        #     "Type of workshop": session.get("Type of workshop", "Unknown")
                        # })
                        slot_entry["sessions"].append({
                            "response_id": session_id,
                            "session_id": session_id,
                            "first_author": session.get("Full name of the first author", "Unknown"),
                            "title": session.get("Workshop title", "Unknown"),
                            "workshop_type": session.get("Type of workshop", "Unknown")
                        })
                    else:
                        print(f"Warning: session_id {session_id} not found in sessions dictionary.")
                day_entry["slots"].append(slot_entry)
            schedule_json["days"].append(day_entry)

        try:
            output_path = os.path.join(tempfile.gettempdir(), "conference_schedule.json")
            with open(output_path, "w") as json_file:
                json.dump(schedule_json, json_file, indent=4)
            print(f"Schedule successfully saved to {output_path}")
        except OSError:
            pass
        return schedule_json

#
# if __name__ == '__main__':
#     file_path = "Input_files/Processed_Workshop_Proposals_final.xlsx"
#     schedule_sessions(file_path)
