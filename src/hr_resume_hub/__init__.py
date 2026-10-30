"""Load the consolidated HR resume Google Sheet into PostgreSQL."""
import truststore

# Trust the Windows certificate store so HTTPS works behind corporate TLS inspection.
# Must run before any module that builds an SSL context (requests, urllib3, psycopg) is used.
truststore.inject_into_ssl()

__version__ = "0.1.0"
