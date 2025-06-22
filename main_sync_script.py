import os
import shutil # Kept for general file operations, though not directly used in sync now
import sys
import time
import subprocess

# --- Configuration Paths ---
# Define the paths for your SD card and local save folders.
# Ensure these paths are accurate for your Steam Deck setup.
SD_CARD_BASE_PATH = "/run/media/deck/MINUI"
SD_CARD_SAVES_PATH = os.path.join(SD_CARD_BASE_PATH, "Saves", "GBA")
LOCAL_SAVES_PATH = "/home/deck/Documents/emulation/Emulation/saves/retroarch/saves"

# Path to the directory containing your external srm-to-sav and sav-to-srm scripts.
# This assumes the 'srm-to-sav' folder is a sub-directory of where main_sync_script.py is.
EXTERNAL_CONVERSION_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "srm-to-sav")

# --- Helper Functions for User Output ---
def display_message(message):
    """Prints a formatted message to the console."""
    print(f"\n--- {message} ---")

# --- Core Logic: File Information Retrieval ---
def get_file_info(directory_path, extensions=None):
    """
    Scans a directory for files with specified extensions and returns their
    'true' base name (without extension or '.gba' suffix), full path, and modification time.
    The 'true' base name is used for consistent comparison between SD and local files.
    Ignores files starting with "._".
    Returns a dictionary: {true_basename_lower: {'path': full_path, 'mtime': mtime, 'ext': actual_ext}}
    """
    files_info = {}
    if not os.path.isdir(directory_path):
        return files_info # Return empty if directory doesn't exist

    for filename in os.listdir(directory_path):
        # --- NEW: Ignore files starting with "._" ---
        if filename.startswith("._"):
            # print(f"Skipping hidden/metadata file: {filename}") # Uncomment for debugging
            continue

        # Split extension. For "game.gba.sav", this gives ("game.gba", ".sav")
        name_part_before_ext, actual_ext = os.path.splitext(filename)

        # Check if the file has one of the desired extensions (case-insensitive)
        if extensions and actual_ext.lower() not in [e.lower() for e in extensions]:
            continue

        # Determine the 'true' base name for comparison (e.g., "my_game" from "my_game.gba.sav")
        true_basename = name_part_before_ext
        if actual_ext.lower() == '.sav' and true_basename.lower().endswith('.gba'):
            true_basename = os.path.splitext(true_basename)[0] # Strips '.gba' from 'game.gba'

        full_path = os.path.join(directory_path, filename)
        if os.path.isfile(full_path):
            try:
                mtime = os.path.getmtime(full_path)
                # Store the true_basename (lowercase) as the key for consistent comparison
                files_info[true_basename.lower()] = {
                    'path': full_path,
                    'mtime': mtime,
                    'ext': actual_ext.lower() # Store the actual extension found
                }
            except OSError as e:
                print(f"Warning: Could not get file information for {full_path}: {e}")
    return files_info

# --- Core Logic: File Comparison ---
def compare_folders(sd_info, local_info):
    """
    Compares file information from SD card and local folders using the 'true' base names.
    Identifies files unique to each location and conflicts (files present in both
    but with different modification times or differing extensions, even if the base name matches).
    """
    differences = {
        'sd_only': [],      # Files found only on the SD card
        'local_only': [],   # Files found only in the local folder
        'conflicts': []     # Files in both, but with differing mtimes or extensions
    }

    # Get a combined list of all unique 'true' basenames (game names) from both locations
    all_basenames = sorted(list(set(sd_info.keys()).union(local_info.keys())))

    for basename in all_basenames:
        sd_file = sd_info.get(basename)
        local_file = local_info.get(basename)

        if sd_file and not local_file:
            # File exists only on SD card
            differences['sd_only'].append(sd_file)
        elif not sd_file and local_file:
            # File exists only in local folder
            differences['local_only'].append(local_file)
        elif sd_file and local_file:
            # File exists in both locations. Check for conflicts.
            # A conflict occurs if modification times differ significantly (more than 1 second)
            # OR if their extensions are different (e.g., .sav on SD, .srm locally)
            # and their modification times are not perfectly identical.
            if abs(sd_file['mtime'] - local_file['mtime']) > 1.0 or sd_file['ext'] != local_file['ext']:
                differences['conflicts'].append({
                    'basename': basename, # The true base name (e.g., 'my_game')
                    'sd': sd_file,
                    'local': local_file
                })
    return differences

# --- Core Logic: Conversion Script Execution ---
def _run_conversion_script(script_name, input_path, output_path):
    """
    Helper function to execute an external conversion script using subprocess.
    IMPORTANT: When passing arguments as a list to subprocess.run (shell=False, the default),
    Python correctly handles spaces in file paths without requiring explicit quotes
    around the path strings themselves. Adding quotes to the strings here would
    make the literal quote characters part of the path, likely causing errors.
    """
    script_full_path = os.path.join(EXTERNAL_CONVERSION_SCRIPTS_DIR, script_name)
    if not os.path.exists(script_full_path):
        raise FileNotFoundError(f"External conversion script not found: {script_full_path}")
    if not os.path.isfile(script_full_path):
        raise FileNotFoundError(f"External conversion script is not a file: {script_full_path}")

    # Ensure the output directory exists before the conversion script tries to write to it.
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # Use sys.executable to ensure the correct python interpreter is used to run the external script.
    # Paths are passed as separate list elements; subprocess.run handles spaces correctly.
    command = [sys.executable, script_full_path, '-i', input_path, '-o', output_path]
    print(f"  Executing conversion: {' '.join(command)}") # Log the command for debugging

    try:
        # Run the subprocess. `check=False` allows us to handle non-zero exit codes manually.
        # `cwd` is set to EXTERNAL_CONVERSION_SCRIPTS_DIR in case the external scripts have relative imports.
        result = subprocess.run(command, capture_output=True, text=True, check=False, cwd=EXTERNAL_CONVERSION_SCRIPTS_DIR)

        if result.returncode != 0:
            # If the external script failed, raise a RuntimeError with its stderr/stdout.
            error_message = (
                f"Conversion failed for {os.path.basename(input_path)} -> {os.path.basename(output_path)} "
                f" using {script_name}.\n"
                f"Stderr: {result.stderr.strip() or 'No Stderr'}\n"
                f"Stdout: {result.stdout.strip() or 'No Stdout'}"
            )
            raise RuntimeError(error_message)
        # print(f"  Conversion successful: {os.path.basename(input_path)} -> {os.path.basename(output_path)}")
        return output_path
    except Exception as e:
        # Catch any other exceptions during subprocess execution
        raise IOError(f"Failed to run conversion command: {e}")

# --- Sync Operations ---
def sync_sd_to_local(sd_info, local_info, differences):
    """
    Syncs files from the SD card to the local folder.
    This direction treats the SD card as the "source of truth".
    It copies and converts .sav files from SD (e.g., 'game.gba.sav')
    to .srm files in the local folder (e.g., 'game.srm').
    """
    display_message("Initiating Sync: SD Card to Local Folder")
    processed_count = 0

    # 1. Process files found ONLY on the SD card
    for sd_file in differences['sd_only']:
        # sd_file['path'] is like /path/to/my_game.gba.sav
        # basename_for_output will be 'my_game'
        basename_for_output = os.path.splitext(os.path.splitext(os.path.basename(sd_file['path']))[0])[0]
        # Target path in local folder will be with .srm extension: /path/to/my_game.srm
        target_path = os.path.join(LOCAL_SAVES_PATH, f"{basename_for_output}.srm")
        try:
            _run_conversion_script("sav-to-srm.py", sd_file['path'], target_path)
            print(f"  -> Copied '{os.path.basename(sd_file['path'])}' (SD) to '{os.path.basename(target_path)}' (Local).")
            processed_count += 1
        except Exception as e:
            print(f"  Error copying/converting '{os.path.basename(sd_file['path'])}' to Local: {e}")

    # 2. Process conflicts (files in both locations that differ)
    for conflict in differences['conflicts']:
        sd_file = conflict['sd']
        local_file = conflict['local']
        # The basename from conflicts is already the 'true' base name (e.g., 'my_game')
        basename_for_output = conflict['basename']

        # Determine which file to prioritize for this sync direction (SD to Local)
        if sd_file['mtime'] > local_file['mtime']:
            # SD card version is newer, so we update the local file
            target_path = os.path.join(LOCAL_SAVES_PATH, f"{basename_for_output}.srm")
            try:
                _run_conversion_script("sav-to-srm.py", sd_file['path'], target_path)
                print(f"  -> Resolved conflict for '{basename_for_output}': Updated Local with newer SD file '{os.path.basename(sd_file['path'])}'.")
                processed_count += 1
            except Exception as e:
                print(f"  Error resolving conflict for '{basename_for_output}' (SD->Local): {e}")
        elif sd_file['ext'] == '.sav' and local_file['ext'] == '.srm' and abs(sd_file['mtime'] - local_file['mtime']) <= 1.0:
             # Case: SD is .sav, Local is .srm, mtimes are practically the same.
             # This implies they are likely the same save just with different extensions.
             # No action needed in this direction as local already has the .srm equivalent.
             print(f"  -> Skipping '{basename_for_output}': Local version already exists as .srm with similar modification time to SD's .sav.")
        else:
            # Local version is newer or preferred in other scenarios.
            print(f"  -> Skipping '{basename_for_output}': Local version appears newer or preferred. Choose Local to SD sync to update SD if desired.")

    display_message(f"SD to Local Sync Complete. {processed_count} files processed/updated.")

def sync_local_to_sd(sd_info, local_info, differences):
    """
    Syncs files from the local folder to the SD card.
    This direction treats the local folder as the "source of truth".
    It copies and converts .srm files from local (e.g., 'game.srm')
    to .sav files on the SD card (e.g., 'game.gba.sav').
    """
    display_message("Initiating Sync: Local Folder to SD Card")
    processed_count = 0

    # 1. Process files found ONLY in the local folder
    for local_file in differences['local_only']:
        # local_file['path'] is like /path/to/my_game.srm
        # basename_for_output will be 'my_game'
        basename_for_output = os.path.splitext(os.path.basename(local_file['path']))[0]
        # Target path on SD card will be with .gba.sav extension: /path/to/my_game.gba.sav
        target_path = os.path.join(SD_CARD_SAVES_PATH, f"{basename_for_output}.gba.sav")
        try:
            _run_conversion_script("srm-to-sav.py", local_file['path'], target_path)
            print(f"  -> Copied '{os.path.basename(local_file['path'])}' (Local) to '{os.path.basename(target_path)}' (SD).")
            processed_count += 1
        except Exception as e:
            print(f"  Error copying/converting '{os.path.basename(local_file['path'])}' to SD: {e}")

    # 2. Process conflicts (files in both locations that differ)
    for conflict in differences['conflicts']:
        sd_file = conflict['sd']
        local_file = conflict['local']
        # The basename from conflicts is already the 'true' base name (e.g., 'my_game')
        basename_for_output = conflict['basename']

        # Determine which file to prioritize for this sync direction (Local to SD)
        if local_file['mtime'] > sd_file['mtime']:
            # Local version is newer, so we update the SD card file
            target_path = os.path.join(SD_CARD_SAVES_PATH, f"{basename_for_output}.gba.sav")
            try:
                _run_conversion_script("srm-to-sav.py", local_file['path'], target_path)
                print(f"  -> Resolved conflict for '{basename_for_output}': Updated SD with newer Local file '{os.path.basename(local_file['path'])}'.")
                processed_count += 1
            except Exception as e:
                print(f"  Error resolving conflict for '{basename_for_output}' (Local->SD): {e}")
        elif sd_file['ext'] == '.sav' and local_file['ext'] == '.srm' and abs(sd_file['mtime'] - local_file['mtime']) <= 1.0:
             # Case: SD is .sav, Local is .srm, mtimes are practically the same.
             # This implies they are likely the same save just with different extensions.
             # No action needed in this direction as SD already has the .sav equivalent.
             print(f"  -> Skipping '{basename_for_output}': SD version already exists as .sav with similar modification time to Local's .srm.")
        else:
            # SD version is newer or preferred in other scenarios.
            print(f"  -> Skipping '{basename_for_output}': SD version appears newer or preferred. Choose SD to Local sync to update Local if desired.")

    display_message(f"Local to SD Sync Complete. {processed_count} files processed/updated.")

def print_differences(differences):
    """Prints the identified differences in a user-friendly format."""
    display_message("Detected Differences:")

    # Check if there are any differences at all
    if not any(differences['sd_only'] or differences['local_only'] or differences['conflicts']):
        print("No significant differences found. Folders are largely in sync.")
        return False # Indicate no differences found

    # Print files found only on SD card
    if differences['sd_only']:
        print("\n--- Files found ONLY on SD Card (will be copied to Local if syncing SD -> Local):")
        for f in differences['sd_only']:
            print(f"  - {os.path.basename(f['path'])} (Last Modified: {time.ctime(f['mtime'])})")

    # Print files found only in local folder
    if differences['local_only']:
        print("\n--- Files found ONLY in Local Folder (will be copied to SD if syncing Local -> SD):")
        for f in differences['local_only']:
            print(f"  - {os.path.basename(f['path'])} (Last Modified: {time.ctime(f['mtime'])})")

    # Print conflicts
    if differences['conflicts']:
        print("\n--- Conflicts (Files present in both, but differ by modification time or extension):")
        for c in differences['conflicts']:
            print(f"\n  - Game Base Name: {c['basename']}")
            print(f"    SD Card : {os.path.basename(c['sd']['path'])} (Last Modified: {time.ctime(c['sd']['mtime'])}, Ext: {c['sd']['ext']})")
            print(f"    Local   : {time.ctime(c['local']['mtime'])}, Ext: {c['local']['ext']})")
            # Suggest which version is newer
            if c['sd']['mtime'] > c['local']['mtime']:
                print("    (SD Card version is NEWER)")
            elif c['local']['mtime'] > c['sd']['mtime']:
                print("    (Local version is NEWER)")
            else:
                print("    (Modification times are similar, but extensions might differ)")
    return True # Indicate differences were found

# --- Main Program Execution Flow ---
def main():
    display_message("GBA Save Sync Tool for Steam Deck")

    # --- Initial Path and Directory Checks ---
    # Ensures the necessary directories exist before proceeding.
    if not os.path.isdir(SD_CARD_BASE_PATH):
        print(f"Error: SD Card base path '{SD_CARD_BASE_PATH}' not found.")
        print("Please ensure your SD card is inserted and properly mounted.")
        print("Exiting.")
        return

    if not os.path.isdir(SD_CARD_SAVES_PATH):
        print(f"Error: SD Card GBA saves path '{SD_CARD_SAVES_PATH}' not found.")
        print("Please ensure the 'minui/Saves/GBA' directory exists on your SD card.")
        print("Exiting.")
        return

    if not os.path.isdir(LOCAL_SAVES_PATH):
        print(f"Warning: Local RetroArch saves path '{LOCAL_SAVES_PATH}' not found.")
        print(f"Attempting to create: {LOCAL_SAVES_PATH}")
        try:
            os.makedirs(LOCAL_SAVES_PATH, exist_ok=True)
            print(f"Successfully created local saves directory: {LOCAL_SAVES_PATH}")
        except OSError as e:
            print(f"Error creating local saves directory: {e}")
            print("Please create it manually or check permissions.")
            print("Exiting.")
            return

    if not os.path.isdir(EXTERNAL_CONVERSION_SCRIPTS_DIR):
        print(f"Error: External conversion scripts directory '{EXTERNAL_CONVERSION_SCRIPTS_DIR}' not found.")
        print("Please ensure the 'srm-to-sav' folder is cloned or copied into your 'gba-rom-save-tool' directory.")
        print("Exiting.")
        return

    # --- Get file information from both locations ---
    # Collects details about save files from both the SD card and local storage.
    # SD card typically uses .sav for GBA saves (e.g., game.gba.sav)
    sd_info = get_file_info(SD_CARD_SAVES_PATH, ['.sav'])
    # RetroArch locally typically uses .srm for save RAM (e.g., game.srm)
    local_info = get_file_info(LOCAL_SAVES_PATH, ['.srm'])

    print(f"Found {len(sd_info)} GBA save files on SD card in '{SD_CARD_SAVES_PATH}'.")
    print(f"Found {len(local_info)} RetroArch save files locally in '{LOCAL_SAVES_PATH}'.")

    # --- Compare and display differences ---
    # Identifies what needs to be synced or reconciled.
    differences = compare_folders(sd_info, local_info)
    has_diff = print_differences(differences)

    if not has_diff:
        print("\nNo pending sync operations required based on current file states.")
        print("Exiting.")
        return

    # --- Prompt user for sync action ---
    # Guides the user to choose the desired sync direction.
    while True:
        print("\n------------------------------------")
        print("Choose a sync direction or exit:")
        print("1. Sync from SD Card to Local Folder (SD is the source of truth)")
        print("   - New files on SD will be copied and converted to Local as .srm.")
        print("   - Newer SD files will overwrite older Local files (converting .gba.sav to .srm).")
        print("2. Sync from Local Folder to SD Card (Local is the source of truth)")
        print("   - New files in Local will be copied and converted to SD as .gba.sav.")
        print("   - Newer Local files will overwrite older SD files (converting .srm to .gba.sav).")
        print("3. Exit without syncing")
        print("------------------------------------")

        choice = input("Enter your choice (1, 2, or 3): ").strip()

        if choice == '1':
            sync_sd_to_local(sd_info, local_info, differences)
            break
        elif choice == '2':
            sync_local_to_sd(sd_info, local_info, differences)
            break
        elif choice == '3':
            print("Exiting without syncing. Goodbye!")
            break
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")

if __name__ == "__main__":
    main()
