import os
import psycopg2
from datetime import datetime

# Database connection parameters: set these in your environment or modify directly
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

def get_connection():
    """Return a new connection to the PostgreSQL database."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

def insert_message(user_id, display_name, message_text):
    """Insert a new message into the messages table and return the new id."""
    conn = get_connection()
    cur = conn.cursor()
    insert_sql = """
        INSERT INTO messages (user_id, display_name, message_text)
        VALUES (%s, %s, %s)
        RETURNING id;
    """
    cur.execute(insert_sql, (user_id, display_name, message_text))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def get_messages():
    """Retrieve all messages ordered by created_at in descending order."""
    conn = get_connection()
    cur = conn.cursor()
    select_sql = """
        SELECT id, user_id, display_name, message_text, created_at
        FROM messages
        ORDER BY created_at DESC;
    """
    cur.execute(select_sql)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def update_message(message_id, new_message_text):
    """Update the message_text of a specific message."""
    conn = get_connection()
    cur = conn.cursor()
    update_sql = """
        UPDATE messages
        SET message_text = %s, created_at = CURRENT_TIMESTAMP
        WHERE id = %s;
    """
    cur.execute(update_sql, (new_message_text, message_id))
    conn.commit()
    cur.close()
    conn.close()

def delete_message(message_id):
    """Delete a message from the messages table by its id."""
    conn = get_connection()
    cur = conn.cursor()
    delete_sql = "DELETE FROM messages WHERE id = %s;"
    cur.execute(delete_sql, (message_id,))
    conn.commit()
    cur.close()
    conn.close()

if __name__ == "__main__":
    # Example usage:
    # 1. Insert a new message
    new_id = insert_message("user123", "Alice", "Hello, world!")
    print("Inserted new message with ID:", new_id)

    # 2. Read all messages
    print("\nCurrent messages:")
    messages = get_messages()
    for msg in messages:
        print(msg)

    # 3. Update the newly inserted message
    print(f"\nUpdating message id {new_id}...")
    update_message(new_id, "Hello, updated message!")
    print("Messages after update:")
    messages = get_messages()
    for msg in messages:
        print(msg)

    # 4. Delete the newly inserted message
    # print(f"\nDeleting message id {new_id}...")
    # delete_message(new_id)
    # print("Messages after deletion:")
    # messages = get_messages()
    # for msg in messages:
    #     print(msg)
