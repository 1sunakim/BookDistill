from bookdistill.api.app import create_app
from bookdistill.logging_config import configure_logging


configure_logging()
app = create_app()
