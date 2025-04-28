import xml.etree.ElementTree as ET
import os
import csv

def extract_elevations_from_gpx(file_path, output_file_name):
    try:
        # Parse the GPX file
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # Define the namespace
        ns = {'default': 'http://www.topografix.com/GPX/1/1'}
        
        # Find all elevation elements
        elevations = []
        for ele in root.findall('.//default:ele', ns):
            elevations.append(ele.text)
        
        # Write the elevations to a CSV file
        output_file_path = os.path.join(os.path.dirname(file_path), output_file_name)
        with open(output_file_path, 'w', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerow(['Elevation'])  # Write header
            for elevation in elevations:
                csv_writer.writerow([elevation])
        
        print(f"Elevations successfully written to {output_file_path}")
    
    except ET.ParseError:
        print("Error parsing the GPX file. Ensure the file is valid.")
    except Exception as e:
        print(f"An error occurred: {e}")

# Path to the GPX file
file_path = r'C:\Users\U1251R0\Documents\Experiments\Kursmaterialien_PY_Udemy\Altitude\WACHAUmarathon_Marathon.gpx'
# Name of the output file
output_file_name = 'elevations.csv'

# Call the function
extract_elevations_from_gpx(file_path, output_file_name)
