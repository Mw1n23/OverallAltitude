import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import numpy as np
from geopy.distance import geodesic

def parse_gpx(file_path):
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        ns = {'default': 'http://www.topografix.com/GPX/1/1'}
        trkpts = root.findall('.//default:trkpt', ns)
        
        waypoints = []
        elevations = []
        
        for trkpt in trkpts:
            lat = float(trkpt.attrib['lat'])
            lon = float(trkpt.attrib['lon'])
            ele = trkpt.find('default:ele', ns)
            if ele is not None:
                elevations.append(float(ele.text))
                waypoints.append((lat, lon))
        
        return waypoints, elevations
    except ET.ParseError:
        raise ValueError("Error parsing the GPX file. Ensure the file is valid.")
    except Exception as e:
        raise RuntimeError(f"An error occurred: {e}")

def smooth_elevations(elevations, window_size=5):
    """Smooth elevation data using a moving average filter."""
    return np.convolve(elevations, np.ones(window_size)/window_size, mode='same')

def handle_outliers(elevations, max_change=50.0):
    """Replace outliers in elevation data with interpolated values."""
    cleaned_elevations = elevations.copy()
    for i in range(1, len(cleaned_elevations)):
        change = abs(cleaned_elevations[i] - cleaned_elevations[i-1])
        if change > max_change:
            # Interpolate between the previous and next point (if available)
            if i < len(cleaned_elevations) - 1:
                cleaned_elevations[i] = (cleaned_elevations[i-1] + cleaned_elevations[i+1]) / 2
            else:
                cleaned_elevations[i] = cleaned_elevations[i-1]  # Use previous value if at the end
    return cleaned_elevations

def calculate_elevation_changes(elevations, step=1, threshold=1.0, window_size=5, max_change=50.0):
    """Calculate total ascent and descent with smoothing, threshold, and outlier handling."""
    # Step 1: Handle outliers
    elevations = handle_outliers(elevations, max_change=max_change)
    
    # Step 2: Smooth the elevation data
    smoothed_elevations = smooth_elevations(elevations, window_size=window_size)
    
    total_ascent = 0
    total_descent = 0
    elevation_changes = []
    
    # Step 3: Calculate elevation changes with threshold
    for i in range(step, len(smoothed_elevations), step):
        change = smoothed_elevations[i] - smoothed_elevations[i-step]
        elevation_changes.append(change)
        if change > threshold:
            total_ascent += change
        elif change < -threshold:
            total_descent += abs(change)
    
    overall_difference = elevations[-1] - elevations[0]
    
    return total_ascent, total_descent, overall_difference, elevation_changes, smoothed_elevations

def calculate_distances(waypoints):
    distances = [0.0]
    for i in range(1, len(waypoints)):
        distance = geodesic(waypoints[i-1], waypoints[i]).kilometers * 1000  # Convert km to meters
        distances.append(distances[-1] + distance)
    return distances

def detect_circles(waypoints, threshold=0.005, min_distance=3):
    visited_segments = set()
    circles = []
    n = len(waypoints)

    for start in range(n):
        if start in visited_segments:
            continue

        for end in range(start + 1, n):
            if geodesic(waypoints[start], waypoints[end]).kilometers < threshold:
                if calculate_circle_distance(waypoints, start, end) >= min_distance:
                    visited_segments.update(range(start, end + 1))
                    circles.append((start, end))
                break
        else:
            continue
        break

    return circles

def calculate_circle_distance(waypoints, start_idx, end_idx):
    total_distance = 0.0
    for i in range(start_idx, end_idx):
        if i < len(waypoints) - 1:
            total_distance += geodesic(waypoints[i], waypoints[i+1]).kilometers * 1000
    return total_distance

def calculate_average_distance(waypoints):
    total_distance = 0.0
    num_distances = len(waypoints) - 1
    for i in range(len(waypoints) - 1):
        total_distance += geodesic(waypoints[i], waypoints[i+1]).kilometers * 1000
    return total_distance / num_distances if num_distances > 0 else 0

def plot_elevation_profile(distances, elevations, smoothed_elevations, waypoints, circles, gesamtanstieg, gesamtabstieg, netto_hoehenunterschied, avg_distance, step):
    # Calculate elevation changes for plotting (using smoothed data)
    total_ascent, total_descent, overall_difference, elevation_changes = calculate_elevation_changes(elevations, step)[:4]

    # Select waypoints and corresponding elevation based on sampling step
    sampled_distances = distances[::step]
    sampled_elevations = smoothed_elevations[::step]
    
    # Calculate slope percentages for sampled points
    slopes = [0.0]
    for i in range(1, len(sampled_elevations)):
        distance_change = sampled_distances[i] - sampled_distances[i-1]
        elevation_change = sampled_elevations[i] - sampled_elevations[i-1]
        if distance_change > 0:
            slope_percentage = (elevation_change / distance_change) * 100
        else:
            slope_percentage = 0
        slopes.append(slope_percentage)
    
    # Plot the elevation profile
    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(distances, smoothed_elevations, label='Wachau Marathon Höhenprofil', color='blue')
    ax1.set_xlabel('Distanz (m)')
    ax1.set_ylabel('Höhe ü.n.N. (m)', color='blue')
    ax1.tick_params(axis='y', labelcolor='black')
    ax1.grid(True)

    # Create a secondary y-axis for slope percentage
    ax2 = ax1.twinx()
    ax2.set_ylabel('Steigung (%)', color='c')
    ax2.tick_params(axis='y', labelcolor='black')
    ax2.plot(sampled_distances, slopes, label='', color='c', linestyle='-', linewidth=0.8)

    # Mark every 5 kilometers
    km_ticks = [i for i in range(0, int(distances[-1]) + 1000, 5000)]
    ax1.set_xticks(km_ticks)
    ax1.set_xticklabels([f'{int(tick / 1000)}' for tick in km_ticks])

    # Calculate the bottom center coordinates for the textbox
    x_center = (distances[0] + distances[-1]) / 2
    y_bottom = min(smoothed_elevations) + 6

    # Add textbox with ascent, descent, overall difference, and average distance
    textstr = '\n'.join((
        f'Messpunkt: {int(step)}',
        f'Gesamtanstieg: {int(gesamtanstieg)} m',
        f'Gesamtabstieg: {int(gesamtabstieg)} m',
        f'Netto Höhenunterschied: {int(netto_hoehenunterschied)} m',
        f'Durchschnittliche Distanz: {int(avg_distance)} m'))

    props = dict(boxstyle='round', facecolor='wheat', alpha=0.9)
    ax1.text(x_center, y_bottom, textstr, transform=ax1.transData, fontsize=8,
             verticalalignment='bottom', horizontalalignment='center', bbox=props)

   
    # Mark circles
    for idx, (start, end) in enumerate(circles):
        ax1.axvline(x=distances[start], color='magenta', linestyle='--', label=f'Start BC{idx+1}' if idx == 0 else "")
        ax1.axvline(x=distances[end], color='magenta', linestyle='--', label=f'End EC{idx+1}' if idx == 0 else "")
        ax1.annotate(f'BC{idx+1}', (distances[start], min(smoothed_elevations) - 0.05), textcoords="offset points", xytext=(2,-10), ha='left', color='magenta')
        ax1.annotate(f'EC{idx+1}', (distances[end], min(smoothed_elevations) - 0.05), textcoords="offset points", xytext=(2,-10), ha='left', color='magenta')

    plt.title('Wachau Marathon Höhenprofil laut GPX Datei')
    plt.show()

# Main execution
file_path = r'C:\Users\U1251R0\Documents\Experiments\Kursmaterialien_PY_Udemy\Altitude\RawMaterial\WACHAUmarathon_Marathon.gpx'

waypoints, elevations = parse_gpx(file_path)

# User-defined parameters
sampling_step = 5
threshold = 1.0  # Minimum elevation change to count (meters)
window_size = 5  # Smoothing window size
max_change = 50.0  # Maximum plausible elevation change between points (meters)

# Calculate elevation changes with smoothing and threshold
gesamtanstieg, gesamtabstieg, netto_hoehenunterschied, elevation_changes, smoothed_elevations = calculate_elevation_changes(
    elevations, 
    step=sampling_step, 
    threshold=threshold, 
    window_size=window_size, 
    max_change=max_change
)

distances = calculate_distances(waypoints)
circles = detect_circles(waypoints, threshold=0.005, min_distance=3)
average_distance = calculate_average_distance(waypoints)

# Print results
print(f"Gesamtanstieg: {gesamtanstieg} m")
print(f"Gesamtabstieg: {gesamtabstieg} m")
print(f"Netto Höhenunterschied: {netto_hoehenunterschied} m")
print(f"Durchschnittliche Distanz zwischen Punkten: {average_distance:.2f} m")
print(f"Detected circles: {circles}")

# Plot the elevation profile with smoothed data
plot_elevation_profile(distances, elevations, smoothed_elevations, waypoints, circles, gesamtanstieg, gesamtabstieg, netto_hoehenunterschied, average_distance, sampling_step)