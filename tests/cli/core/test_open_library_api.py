from cli.core import open_library_api

def test_resolve_valid_isbn13():
    valid_isbn13 = "978-1032963389"
    isbn13 = open_library_api.resolve_isbn13(valid_isbn13, None)
    assert isbn13 == valid_isbn13

def test_resolve_valid_isbn13_number():
    valid_isbn13 = 9781032963389
    isbn13 = open_library_api.resolve_isbn13(valid_isbn13, None)
    assert isbn13 == valid_isbn13

def test_resolve_valid_isbn10_to_isbn13():
    valid_isbn13 = "9781032963389"
    valid_isbn10 = "1032963387"
    isbn13 = open_library_api.resolve_isbn13(None, valid_isbn10)
    assert isbn13 == valid_isbn13

def test_resolve_invalid_isbn13():
    valid_isbn13 = "978-10329XXC389"
    isbn13 = open_library_api.resolve_isbn13(valid_isbn13, None)
    assert isbn13 == valid_isbn13

def test_resolve_invalid_isbn10_to_isbn13():
    valid_isbn10 = "10329XXC387"
    isbn13 = open_library_api.resolve_isbn13(None, valid_isbn10)
    assert isbn13 == None

def test_resolve_invalid_isbn13_and_isbn10():
    isbn13 = open_library_api.resolve_isbn13(None, None)
    assert None == None


def test_parse_valid_publication_year():
    date = "13 July 2026"
    parsed_date = open_library_api.parse_publication_year(date)
    assert parsed_date == 2026

def test_parse_invalid_publication_year():
    date = None
    parsed_date = open_library_api.parse_publication_year(date)
    assert parsed_date == None

def test_parse_old_publication_year():
    date = "May 1870"
    parsed_date = open_library_api.parse_publication_year(date)
    assert parsed_date == None

def test_parse_future_publication_year():
    date = "April 2049"
    parsed_date = open_library_api.parse_publication_year(date)
    assert parsed_date == 2049

def test_parse_invalid_future_publication_year():
    date = "The distance future, the year thousand"
    parsed_date = open_library_api.parse_publication_year(date)
    assert parsed_date == None

def test_open_library_urls():
    valid_isbn13 = "9781032963389"
    urls = open_library_api.open_library_urls(valid_isbn13)
    assert urls == ('https://openlibrary.org/isbn/9781032963389', 'https://openlibrary.org/search?q=isbn:9781032963389')

def test_open_invalid_library_urls():
    valid_isbn13 = "badbaddog"
    urls = open_library_api.open_library_urls(valid_isbn13)
    assert urls == ('https://openlibrary.org/isbn/badbaddog', 'https://openlibrary.org/search?q=isbn:badbaddog')


# extract_fields
# resolve_authors
# extract_work_key
# extract_language_codes
# extract_physical_format
# extract_grouping_fields
