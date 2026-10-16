import pandas as pd
import re
from io import BytesIO

class FileTransformer:
    def __init__(self):
        self.model = 'Llama3.2'
        self.data = None
        self.processed_excel_bytes = None

    def extract_co_authors(self, value):
        if pd.isna(value):
            return []
        names = []
        for item in re.split(r'[;,]', str(value)):
            clean_item = item.strip()
            clean_item = re.sub(r'<[^>]*>', '', clean_item)
            clean_item = re.sub(r'\([^)]*@[^)]*\)', '', clean_item)
            clean_item = re.sub(r'\b\S+@\S+\b', '', clean_item)
            clean_item = re.sub(r'author\s*\d+', '', clean_item, flags=re.IGNORECASE)
            clean_item = re.sub(r'\s+', ' ', clean_item).strip()
            if clean_item:
                names.append(clean_item)
        return names


    def generate_structured_file (self, data): # input_file_path):
        self.data = data
        row_count = len(data)

        def get_series(column_name, default_value=""):
            if column_name in data.columns:
                return data[column_name]
            return pd.Series([default_value] * row_count)
        # Load the data from the provided Excel file
        # input_file= input_file_path #'Input_files\\JTELSS24_workshop_proposals_22.xlsx'
        # data = pd.read_excel(input_file, sheet_name='data')
        # data = data
        # Extract the columns of interest for the output
        columns_to_extract = {
            'Response ID': 'Submission_id',
            'Session ID': 'Submission_id',
            'Full name of the first author': 'Full name of the first author',
            'Co-authors': 'Co-author(s) who also plan to attend the event in person',
            'Type of workshop': 'Type of workshop',
            'Workshop title': 'Workshop title',
            'Needs analysis': 'Needs analysis',
            'Learning objectives': 'Learning objectives',
            'Pre-activities and prerequisites': 'Pre-activities and prerequisites',
            'Session description': 'Session description',
            'Scheduling': 'Scheduling',
            'Preferred duration in the main program.': 'Preferred duration in the main program.'
        }

        # Process the data into the required format
        output_data = pd.DataFrame()

        # Map columns directly
        for output_col, input_col in columns_to_extract.items():
            output_data[output_col] = get_series(input_col)

        output_data['Co-authors'] = get_series('Co-author(s) who also plan to attend the event in person').apply(
            self.extract_co_authors
        )

        # Persist the Excel payload in memory for deployments without writable disks
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            output_data.to_excel(writer, index=False)
        buffer.seek(0)
        self.processed_excel_bytes = buffer.getvalue()

        # print(f"Processed data saved to {output_file}")
        return output_data