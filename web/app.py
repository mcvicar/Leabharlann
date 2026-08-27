from flask import Flask, render_template, abort

from shared.database import get_db_connection
from shared import queries
from psycopg.rows import dict_row

app = Flask(__name__)

@app.route("/")
def library():
    try:
        with get_db_connection() as conn:
            library = queries.get_all_books(conn)
    except RuntimeError as err:
        return {"error": "Database access failed"}
    
    return render_template('index.html', library=library)

@app.route("/works/<path:work_key>")
def work(work_key):
    if not work_key.startswith("/"):
        work_key = "/" + work_key
    try: 
        with get_db_connection() as conn:
            work = queries.get_work_by_key(conn, work_key)
    except RuntimeError as err:
        return {"error": "Database access failed"}
    
    if work is None:
        abort(404)

    return render_template('work.html', work=work)


@app.route("/author/<path:author_name>")
def author(author_name):
    try:
        with get_db_connection() as conn:
            works = queries.get_works_by_author(conn, author_name)
    except RuntimeError as err:
        return {"error": "Database access failed"}
    
    if works is None:
        abort(404)

    return render_template('author.html', works=works, author=author_name)