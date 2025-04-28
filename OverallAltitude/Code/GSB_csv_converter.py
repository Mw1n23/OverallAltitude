import pyproj
import pandas as pd
import os

def gsb_to_csv(gsb_file, output_file):
    try:
        # Initialize the grid shift file
        grid = pyproj.GeodeticGrid(gsb_file)
        
        # Create lists to store the data
        longitudes = []
        latitudes = []
        shift_lat = []
        shift_lon = []
        accuracy = []
        
        # Get grid size
        grid_width = grid.grid_width
        grid_height = grid.grid_height
        
        # Iterate over the grid and extract data
        for i in range(grid_width):
            for j in range(grid_height):
                lon, lat, dlat, dlon, accuracy_value = grid.get_grid_point(i, j)
                longitudes.append(lon)
                latitudes.append(lat)
                shift_lat.append(dlat)
                shift_lon.append(dlon)
                accuracy.append(accuracy_value)
        
        # Create a DataFrame
        df = pd.DataFrame({
            'Longitude': longitudes,
            'Latitude': latitudes,
            'Delta_Latitude': shift_lat,
            'Delta_Longitude': shift_lon,
            'Accuracy': accuracy
        })
        
        # Save to CSV
        df.to_csv(output_file, index=False)
        print(f"Data successfully saved to {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

# Define file paths
input_file = r'C:\Users\U1251R0\Documents\Experiments\Kursmaterialien_PY_Udemy\Altitude\RawMaterial\AT_GIS_GRID_2021_09_28.gsb'
output_file = os.path.join(os.path.dirname(input_file), 'Altitude_data.csv')

# Convert .gsb to CSV
gsb_to_csv(input_file, output_file)


def gsb_to_csv(gsb_file, output_file):
    try:
        # Initialize the grid shift file
        # Note: pyproj does not have GeodeticGrid; this is a placeholder.
        # Replace this with appropriate library and methods for .gsb files.
        grid = pyproj.Transformer.from_proj(
            pyproj.Proj(proj='latlong', datum='WGS84'), 
            pyproj.Proj(proj='latlong', datum='WGS84')
        )
        
        # Example to get the grid data (you might need a specific library for .gsb)
        # Dummy data for demonstration
        longitudes = [10.0, 10.5, 11.0]
        latitudes = [50.0, 50.5, 51.0]
        shift_lat = [0.1, 0.1, 0.1]
        shift_lon = [0.1, 0.1, 0.1]
        accuracy = [1, 1, 1]
        
        # Create a DataFrame
        df = pd.DataFrame({
            'Longitude': longitudes,
            'Latitude': latitudes,
            'Delta_Latitude': shift_lat,
            'Delta_Longitude': shift_lon,
            'Accuracy': accuracy
        })
        
        # Save to CSV
        df.to_csv(output_file, index=False)
        print(f"Data successfully saved to {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

# Define file paths
input_file = r'C:\Users\U1251R0\Documents\Experiments\Kursmaterialien_PY_Udemy\Altitude\RawMaterial\AT_GIS_GRID_2021_09_28.gsb'
output_file = os.path.join(os.path.dirname(input_file), 'Altitude_data.csv')

# Convert .gsb to CSV
gsb_to_csv(input_file, output_file)
