UPDATE books
SET
    physical_format = NULLIF(physical_format, 'None'),
    publish_date = NULLIF(publish_date, 'None'),
    edition = NULLIF(edition, 'None');