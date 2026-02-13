"""Database models package."""
from models.database import Base, get_session, init_db, close_db, get_pool_status, get_engine
from models.user import User
from models.organization import Organization
from models.pipeline import Pipeline, PipelineStage
from models.deal import Deal
from models.account import Account
from models.contact import Contact
from models.activity import Activity
from models.meeting import Meeting
from models.artifact import Artifact
from models.conversation import Conversation
from models.chat_message import ChatMessage
from models.integration import Integration
from models.pending_operation import PendingOperation, CrmOperation  # CrmOperation is alias
from models.agent_task import AgentTask
from models.workflow import Workflow, WorkflowRun
from models.user_tool_setting import UserToolSetting
from models.change_session import ChangeSession
from models.record_snapshot import RecordSnapshot
from models.slack_user_mapping import SlackUserMapping
from models.shared_file import SharedFile
from models.user_memory import UserMemory
from models.organization_membership import OrganizationMembership
from models.goal import Goal
from models.tracker_team import TrackerTeam
from models.tracker_project import TrackerProject
from models.tracker_issue import TrackerIssue

__all__ = [
    "Base",
    "get_session",
    "init_db",
    "User",
    "Organization",
    "Pipeline",
    "PipelineStage",
    "Deal",
    "Account",
    "Contact",
    "Activity",
    "Meeting",
    "Artifact",
    "Conversation",
    "ChatMessage",
    "Integration",
    "PendingOperation",
    "CrmOperation",  # Alias for PendingOperation (backward compat)
    "AgentTask",
    "Workflow",
    "WorkflowRun",
    "UserToolSetting",
    "ChangeSession",
    "RecordSnapshot",
    "SlackUserMapping",
    "SharedFile",
    "UserMemory",
    "OrganizationMembership",
    "Goal",
    "TrackerTeam",
    "TrackerProject",
    "TrackerIssue",
]
