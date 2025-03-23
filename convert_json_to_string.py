import json
import sys

def main():
    # Check if the user provided the JSON file path as an argument.
    if len(sys.argv) != 2:
        print("Usage: python convert_json_to_string.py <path_to_json_file>")
        sys.exit(1)

    json_file_path = sys.argv[1]

    try:
        with open(json_file_path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except Exception as e:
        print(f"Error reading or parsing JSON file: {e}")
        sys.exit(1)

    # Convert the JSON data into a string
    credentials_string = json.dumps(data)
    print(credentials_string)

if __name__ == "__main__":
    main()
