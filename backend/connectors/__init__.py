"""Data connectors package."""
from connectors.apollo import ApolloConnector
from connectors.asana import AsanaConnector
from connectors.base import BaseConnector
from connectors.fireflies import FirefliesConnector
from connectors.github import GitHubConnector
from connectors.gmail import GmailConnector
from connectors.google_calendar import GoogleCalendarConnector
from connectors.google_drive import GoogleDriveConnector
from connectors.hubspot import HubSpotConnector
from connectors.microsoft_calendar import MicrosoftCalendarConnector
from connectors.microsoft_mail import MicrosoftMailConnector
from connectors.salesforce import SalesforceConnector
from connectors.slack import SlackConnector
from connectors.zoom import ZoomConnector

__all__ = [
    "ApolloConnector",
    "AsanaConnector",
    "BaseConnector",
    "FirefliesConnector",
    "GitHubConnector",
    "GmailConnector",
    "GoogleCalendarConnector",
    "GoogleDriveConnector",
    "HubSpotConnector",
    "MicrosoftCalendarConnector",
    "MicrosoftMailConnector",
    "SalesforceConnector",
    "SlackConnector",
    "ZoomConnector",
]
