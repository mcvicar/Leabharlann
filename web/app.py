from flask import Flask, render_template, abort

from shared.database import get_db_connection
from shared import queries
from psycopg.rows import dict_row
import pprint

app = Flask(__name__)

@app.route("/")
def library():
    with get_db_connection() as conn:
        library = queries.get_all_books(conn)

    return render_template('index.html', library=library)

@app.route("/works/<path:work_key>")
def work(work_key):
    if not work_key.startswith("/"):
        work_key = "/" + work_key

    with get_db_connection() as conn:
        work = queries.get_work_by_key(conn, work_key)

    if work is None:
        abort(404)

    return render_template('work.html', work=work)


@app.route("/author/<path:author_name>")
def author(author_name):
    with get_db_connection() as conn:
        works = queries.get_works_by_author(conn, author_name)

    if works is None:
        abort(404)

    return render_template('author.html', works=works, author=author_name)

@app.route("/debug/keys")
def debug_key():
    with get_db_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            keys = cur.execute("Select title, work_key, isbn13, publish_date, edition from books limit 5").fetchall()
            formatted_data = pprint.pformat(keys, indent=2)
            return f"<pre>{formatted_data}</pre>"