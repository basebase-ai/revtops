"""Data connectors package."""
from connectors.base import BaseConnector
from connectors.gmail import GmailConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.hubspot import HubSpotConnector
from connectors.microsoft_calendar import MicrosoftCalendarConnector
from connectors.microsoft_mail import MicrosoftMailConnector
from connectors.salesforce import SalesforceConnector
from connectors.slack import SlackConnector

__all__ = [
    "BaseConnector",
    "GmailConnector",
    "GoogleCalendarConnector",
    "HubSpotConnector",
    "MicrosoftCalendarConnector",
    "MicrosoftMailConnector",
    "SalesforceConnector",
    "SlackConnector",
]
