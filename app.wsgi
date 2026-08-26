import sys
import logging
import site
import traceback

logging.basicConfig(stream=sys.stderr)


def application(environ, start_response):
    try:
        site.addsitedir('/home/pi/.my_venv/lib/python3.11/site-packages')
        sys.path.insert(0, '/home/pi/develop/book-ingest')

        from web.app import app
        return app(environ, start_response)

    except Exception:
        error_traceback = traceback.format_exc().encode('utf-8')
        status = '500 Internal server error'
        response_headers = [
            ('Content-type', 'text/plain'),
            ('Content-Length', str(len(error_traceback)))
        ]
        start_response(status, response_headers)
        return [error_traceback]