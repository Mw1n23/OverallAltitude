import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import numpy as np
from geopy.distance import geodesic
import os
import argparse

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
            if i < len(cleaned_elevations) - 1:
                cleaned_elevations[i] = (cleaned_elevations[i-1] + cleaned_elevations[i+1]) / 2
            else:
                cleaned_elevations[i] = cleaned_elevations[i-1]
    return cleaned_elevations

def calculate_elevation_changes(elevations, step=1, threshold=1.0, window_size=5, max_change=50.0):
    """Calculate total ascent and descent with smoothing, threshold, and outlier handling."""
    elevations = handle_outliers(elevations, max_change=max_change)
    smoothed_elevations = smooth_elevations(elevations, window_size=window_size)
    
    total_ascent = 0
    total_descent = 0
    elevation_changes = []
    
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
        distance = geodesic(waypoints[i-1], waypoints[i]).kilometers * 1000
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
    total_ascent, total_descent, overall_difference, elevation_changes = calculate_elevation_changes(elevations, step)[:4]

    sampled_distances = distances[::step]
    sampled_elevations = smoothed_elevations[::step]
    
    slopes = [0.0]
    for i in range(1, len(sampled_elevations)):
        distance_change = sampled_distances[i] - sampled_distances[i-1]
        elevation_change = sampled_elevations[i] - sampled_elevations[i-1]
        if distance_change > 0:
            slope_percentage = (elevation_change / distance_change) * 100
        else:
            slope_percentage = 0
        slopes.append(slope_percentage)
    
    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(distances, smoothed_elevations, label='Wachau Marathon Höhenprofil', color='blue')
    ax1.set_xlabel('Distanz (m)')
    ax1.set_ylabel('Höhe ü.n.N. (m)', color='blue')
    ax1.tick_params(axis='y', labelcolor='black')
    ax1.grid(True)

    ax2 = ax1.twinx()
    ax2.set_ylabel('Steigung (%)', color='c')
    ax2.tick_params(axis='y', labelcolor='black')
    ax2.plot(sampled_distances, slopes, label='', color='c', linestyle='-', linewidth=0.8)

    km_ticks = [i for i in range(0, int(distances[-1]) + 1000, 5000)]
    ax1.set_xticks(km_ticks)
    ax1.set_xticklabels([f'{int(tick / 1000)}' for tick in km_ticks])

    x_center = (distances[0] + distances[-1]) / 2
    y_bottom = min(smoothed_elevations) + 6

    textstr = '\n'.join((
        f'Messpunkt: {int(step)}',
        f'Gesamtanstieg: {int(gesamtanstieg)} m',
        f'Gesamtabstieg: {int(gesamtabstieg)} m',
        f'Netto Höhenunterschied: {int(netto_hoehenunterschied)} m',
        f'Durchschnittliche Distanz: {int(avg_distance)} m'))

    props = dict(boxstyle='round', facecolor='wheat', alpha=0.9)
    ax1.text(x_center, y_bottom, textstr, transform=ax1.transData, fontsize=8,
             verticalalignment='bottom', horizontalalignment='center', bbox=props)

    for idx, (start, end) in enumerate(circles):
        ax1.axvline(x=distances[start], color='magenta', linestyle='--', label=f'Start BC{idx+1}' if idx == 0 else "")
        ax1.axvline(x=distances[end], color='magenta', linestyle='--', label=f'End EC{idx+1}' if idx == 0 else "")
        ax1.annotate(f'BC{idx+1}', (distances[start], min(smoothed_elevations) - 0.05), textcoords="offset points", xytext=(2,-10), ha='left', color='magenta')
        ax1.annotate(f'EC{idx+1}', (distances[end], min(smoothed_elevations) - 0.05), textcoords="offset points", xytext=(2,-10), ha='left', color='magenta')

    plt.title('Wachau Marathon Höhenprofil laut GPX Datei')
    plt.show()

def main():
    # Set up argument parser for command-line input
    parser = argparse.ArgumentParser(description="Analyze elevation gain from a GPX track.")
    parser.add_argument('--gpx-file', type=str, default=os.path.join('..', 'RawMaterial', 'WACHAUmarathon_Marathon.gpx'),
                        help='Path to the GPX file (default: ../RawMaterial/WACHAUmarathon_Marathon.gpx)')
    parser.add_argument('--sampling-step', type=int, default=5,
                        help='Sampling step for elevation changes (default: 5)')
    parser.add_argument('--threshold', type=float, default=1.0,
                        help='Minimum elevation change to count (meters, default: 1.0)')
    parser.add_argument('--window-size', type=int, default=5,
                        help='Smoothing window size (default: 5)')
    parser.add_argument('--max-change', type=float, default=50.0,
                        help='Maximum plausible elevation change between points (meters, default: 50.0)')

    args = parser.parse_args()

    # Use relative path for the GPX file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    gpx_file_path = os.path.join(script_dir, args.gpx_file)

    # Check if the GPX file exists
    if not os.path.exists(gpx_file_path):
        raise FileNotFoundError(f"GPX file not found at: {gpx_file_path}")

    # Parse the GPX file
    waypoints, elevations = parse_gpx(gpx_file_path)

    # Calculate elevation changes
    gesamtanstieg, gesamtabstieg, netto_hoehenunterschied, elevation_changes, smoothed_elevations = calculate_elevation_changes(
        elevations,
        step=args.sampling_step,
        threshold=args.threshold,
        window_size=args.window_size,
        max_change=args.max_change
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

    # Plot the elevation profile
    plot_elevation_profile(distances, elevations, smoothed_elevations, waypoints, circles, gesamtanstieg, gesamtabstieg, netto_hoehenunterschied, average_distance, args.sampling_step)

if __name__ == "__main__":
    main()
