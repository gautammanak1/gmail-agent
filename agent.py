from datetime import datetime, timezone, UTC
from uuid import uuid4
import os
from dotenv import load_dotenv
from openai import OpenAI
from uagents import Context, Protocol, Agent
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from composio import Composio
import json
import base64
import re
from typing import Optional, Dict, Any, List

# Load environment variables
load_dotenv()

# Initialize clients
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
if not os.getenv("OPENAI_API_KEY"):
    raise ValueError("OPENAI_API_KEY environment variable is required.")

composio_client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# Initialize uAgent
agent = Agent(
    name="Gmail-ASI-Agent",
    seed="Gmail-ASI-Agent",
    port=8001,
    mailbox=True,
)
protocol = Protocol(spec=chat_protocol_spec)

class GmailAgent:
    def __init__(self, user_email: str, auth_config_id: str):
        """
        Initialize the Gmail Agent
        Args:
            user_email: User's email address
            auth_config_id: Gmail auth config ID from Composio dashboard
        """
        self.user_email = user_email
        self.auth_config_id = auth_config_id
        self.composio = composio_client
        self.openai_client = openai_client
        self.tools = None
        self.connected_account = None
        self.connection_request = None
        self.gmail_tools = [
            "GMAIL_CREATE_EMAIL_DRAFT",
            "GMAIL_DELETE_DRAFT",
            "GMAIL_DELETE_MESSAGE",
            "GMAIL_FETCH_EMAILS",
            "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID",
            "GMAIL_GET_ATTACHMENT",
            "GMAIL_LIST_DRAFTS",
            "GMAIL_MOVE_TO_TRASH",
            "GMAIL_PATCH_LABEL",
            "GMAIL_REPLY_TO_THREAD",
            "GMAIL_SEARCH_PEOPLE",
            "GMAIL_SEND_DRAFT",
            "GMAIL_SEND_EMAIL",
            "GMAIL_ADD_LABEL_TO_EMAIL",
            "GMAIL_CREATE_LABEL",
            "GMAIL_FETCH_MESSAGE_BY_THREAD_ID",
            "GMAIL_GET_CONTACTS",
            "GMAIL_GET_PEOPLE",
            "GMAIL_GET_PROFILE",
            "GMAIL_LIST_LABELS",
            "GMAIL_LIST_THREADS",
            "GMAIL_MODIFY_THREAD_LABELS",
            "GMAIL_REMOVE_LABEL",
            "GMAIL_REPLY_TO_EMAIL",
            "GMAIL_MARK_AS_READ",
            "GMAIL_MARK_AS_UNREAD",
            "GMAIL_SEARCH_EMAILS",
            "GMAIL_CREATE_DRAFT"
        ]

    def initiate_auth(self) -> str:
        """Initiate Gmail authentication and return the URL"""
        try:
            print(f"🔐 Initiating Gmail auth for {self.user_email}...")
            self.connection_request = self.composio.connected_accounts.initiate(
                user_id=self.user_email,
                auth_config_id=self.auth_config_id,
            )
            return f"Please visit this URL to authenticate Gmail: {self.connection_request.redirect_url}\nAfter completing, send 'Auth complete' or your next query."
        except Exception as e:
            return f"Error initiating auth: {str(e)}"

    def complete_auth(self) -> bool:
        """Complete authentication by checking connection status"""
        if not self.connection_request:
            return False
        try:
            print("⏳ Checking for authentication completion...")
            self.connected_account = self.connection_request.wait_for_connection(timeout=5)
            self.tools = self.composio.tools.get(user_id=self.user_email, toolkits=["GMAIL"])
            print("✅ Gmail authentication successful!")
            print(f"📧 Available tools: {len(self.gmail_tools)} Gmail tools loaded")
            print("Example queries:")
            print("- Create a label called 'fetch.ai'")
            print("- Get my profile")
            print("- Read emails from google-maps-noreply@google.com")
            print("- Move emails from john@example.com to trash")
            print("- List my contacts")
            print("- Delete spam mail")
            return True
        except TimeoutError:
            return False
        except Exception as e:
            print(f"❌ Auth completion failed: {str(e)}")
            return False

    def is_authenticated(self) -> bool:
        """Check if Gmail is authenticated"""
        return self.connected_account is not None and self.tools is not None

    def process_query(self, user_query: str) -> Dict[str, Any]:
        """
        Process natural language query and execute appropriate Gmail actions
        Args:
            user_query: Natural language query from user
        Returns:
            Result of the executed action
        """
        # Strip @composio agent prefix
        cleaned_query = re.sub(r'^@composio\s+agent\s+', '', user_query, flags=re.IGNORECASE).strip()
        intent_analysis = self.analyze_user_intent(cleaned_query)
        intent = intent_analysis.get("intent", "UNKNOWN")
        parameters = intent_analysis.get("parameters", {})
        confidence = intent_analysis.get("confidence", 0.0)
        print(f"🧠 Detected intent: {intent} (confidence: {confidence}) for query: {cleaned_query}")

        if intent == "AUTH":
            auth_url = self.initiate_auth()
            return {"success": True, "intent": "AUTH", "formatted_result": auth_url}

        if not self.is_authenticated():
            return {"success": False, "error": "Gmail not authenticated. Send 'Authenticate Gmail' to start auth."}

        try:
            if intent == "SEND":
                result = self._handle_send_email(parameters)
            elif intent == "SEARCH":
                result = self._handle_search_emails(parameters)
            elif intent == "DELETE":
                result = self._handle_delete_emails(parameters)
            elif intent == "MOVE_TO_TRASH":
                result = self._handle_move_to_trash(parameters)
            elif intent == "GET_CONTACTS":
                result = self._handle_get_contacts(parameters)
            elif intent == "MARK_READ":
                result = self._handle_mark_as_read(parameters)
            elif intent == "MARK_UNREAD":
                result = self._handle_mark_as_unread(parameters)
            elif intent == "READ":
                result = self._handle_read_email(parameters)
            elif intent == "CREATE_LABEL":
                result = self._handle_create_label(parameters)
            elif intent == "GET_PROFILE":
                result = self._handle_get_profile(parameters)
            else:
                system_prompt = f"""You are a Gmail assistant for {self.user_email} with access to all Gmail tools: {', '.join(self.gmail_tools)}.
                Use the most appropriate tool for the user's request. Provide clear, actionable responses."""
                response = self.openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    tools=self.tools,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": cleaned_query}
                    ],
                )
                raw_result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
                result = {
                    "success": True,
                    "query": cleaned_query,
                    "intent": intent,
                    "parameters": parameters,
                    "result": raw_result,
                    "formatted_result": self._format_result(raw_result, cleaned_query),
                    "model_response": response.choices[0].message.content or "Action completed successfully"
                }
            refined_result = self.refine_response_with_gpt(result.get("formatted_result", "Action completed successfully"))
            result["formatted_result"] = refined_result
            print(f"📤 Sending response to AgentVerse: {refined_result[:100]}...")
            return result
        except Exception as e:
            error_msg = f"❌ Error processing query: {str(e)}"
            print(error_msg)
            return {"success": False, "error": error_msg, "query": cleaned_query}

    def _handle_send_email(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle email sending with AI composition"""
        try:
            recipient = parameters.get("recipient", "")
            if not recipient:
                return {"error": "No recipient specified"}
            composed_email = self.compose_email_with_ai(
                recipient=recipient,
                subject=parameters.get("subject", ""),
                content=parameters.get("content", ""),
                context=parameters.get("context", "")
            )
            prompt = f"""Send an email to {recipient} with:
            Subject: {composed_email['subject']}
            Body: {composed_email['body']}"""
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_SEND_EMAIL to send emails."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Send email to {recipient}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Send email to {recipient}",
                "intent": "SEND",
                "result": result,
                "composed_email": composed_email,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_search_emails(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle email search, including spam emails"""
        try:
            query_parts = []
            if parameters.get("sender"):
                query_parts.append(f"from:{parameters['sender']}")
            if parameters.get("subject"):
                query_parts.append(f"subject:{parameters['subject']}")
            if parameters.get("query"):
                query_parts.append(parameters["query"])
            if parameters.get("is_spam"):
                query_parts.append("is:spam")
            query = " ".join(query_parts) if query_parts else "recent emails"
            prompt = f"Search Gmail for emails matching: {query}. Include full details and body content."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_FETCH_EMAILS to search and retrieve emails with full content."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Search for: {query}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Search for: {query}",
                "intent": "SEARCH",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_delete_emails(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle email deletion, including spam emails"""
        try:
            email_id = parameters.get("email_id", "")
            query = parameters.get("query", "")
            if parameters.get("is_spam"):
                query = "is:spam " + query if query else "is:spam"
            prompt = f"Delete emails matching the criteria: {query or email_id}. First search for matching emails, then delete them using GMAIL_DELETE_MESSAGE."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "For DELETE operations, use GMAIL_FETCH_EMAILS to find emails, then GMAIL_DELETE_MESSAGE for each one."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Delete emails: {query or email_id}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Delete emails: {query or email_id}",
                "intent": "DELETE",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_move_to_trash(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle moving emails to trash"""
        try:
            query_parts = []
            if parameters.get("sender"):
                query_parts.append(f"from:{parameters['sender']}")
            if parameters.get("subject"):
                query_parts.append(f"subject:{parameters['subject']}")
            if parameters.get("query"):
                query_parts.append(parameters["query"])
            query = " ".join(query_parts) if query_parts else "recent emails"
            prompt = f"Move emails matching '{query}' to trash using GMAIL_MOVE_TO_TRASH."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_FETCH_EMAILS to find emails, then GMAIL_MOVE_TO_TRASH for each matching email."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Move to trash: {query}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Move to trash: {query}",
                "intent": "MOVE_TO_TRASH",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_get_contacts(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle listing contacts"""
        try:
            prompt = "Fetch the list of contacts using GMAIL_GET_CONTACTS. Include names and email addresses."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_GET_CONTACTS to retrieve contacts with emailAddresses and names."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_contacts(result[0].get("data", {}) if result else {})
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": "List contacts",
                "intent": "GET_CONTACTS",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_mark_as_read(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle marking emails as read"""
        try:
            email_id = parameters.get("email_id", "")
            query = parameters.get("query", "")
            prompt = f"Mark emails matching '{query or email_id}' as read using GMAIL_MARK_AS_READ."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_FETCH_EMAILS to find emails, then GMAIL_MARK_AS_READ for each one."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Mark as read: {query or email_id}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Mark as read: {query or email_id}",
                "intent": "MARK_READ",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_mark_as_unread(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle marking emails as unread"""
        try:
            email_id = parameters.get("email_id", "")
            query = parameters.get("query", "")
            prompt = f"Mark emails matching '{query or email_id}' as unread using GMAIL_MARK_AS_UNREAD."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_FETCH_EMAILS to find emails, then GMAIL_MARK_AS_UNREAD for each one."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Mark as unread: {query or email_id}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Mark as unread: {query or email_id}",
                "intent": "MARK_UNREAD",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_read_email(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle reading emails with full body content"""
        try:
            query_parts = []
            if parameters.get("sender"):
                query_parts.append(f"from:{parameters['sender']}")
            if parameters.get("subject"):
                query_parts.append(f"subject:{parameters['subject']}")
            if parameters.get("query"):
                query_parts.append(parameters["query"])
            query = " ".join(query_parts) if query_parts else "recent emails"
            prompt = f"Fetch emails matching '{query}' using GMAIL_FETCH_EMAILS and include full body content."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_FETCH_EMAILS to retrieve emails with full body content."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_emails_with_full_content(result[0].get("data", {}).get("messages", []) if result else [])
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Read emails: {query}",
                "intent": "READ",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_create_label(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle creating a new Gmail label"""
        try:
            label_name = parameters.get("label_name", "")
            if not label_name:
                return {"error": "No label name specified"}
            prompt = f"Create a Gmail label named '{label_name}' using GMAIL_CREATE_LABEL."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_CREATE_LABEL to create a new label."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_result(result, f"Create label: {label_name}")
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": f"Create label: {label_name}",
                "intent": "CREATE_LABEL",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_get_profile(self, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Handle fetching Gmail profile"""
        try:
            prompt = "Fetch Gmail profile information using GMAIL_GET_PROFILE."
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                tools=self.tools,
                messages=[
                    {"role": "system", "content": "Use GMAIL_GET_PROFILE to retrieve profile details like email address and message counts."},
                    {"role": "user", "content": prompt}
                ],
            )
            result = self.composio.provider.handle_tool_calls(response=response, user_id=self.user_email)
            formatted_result = self._format_profile(result[0].get("data", {}) if result else {})
            refined_result = self.refine_response_with_gpt(formatted_result)
            return {
                "success": True,
                "query": "Get profile",
                "intent": "GET_PROFILE",
                "result": result,
                "formatted_result": refined_result
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _format_result(self, result: Any, query: str) -> str:
        """Format the result into a readable string"""
        try:
            if not result or not isinstance(result, list):
                return "No data returned from the operation."
            formatted_output = []
            for item in result:
                if not item.get("successful", False):
                    formatted_output.append(f"❌ Error: {item.get('error', 'Unknown error')}")
                    continue
                data = item.get("data", {})
                if "messages" in data:
                    formatted_output.extend(self._format_emails(data["messages"]))
                elif "drafts" in data:
                    formatted_output.extend(self._format_drafts(data["drafts"]))
                elif "labels" in data:
                    formatted_output.extend(self._format_labels(data["labels"]))
                elif "response_data" in data:
                    formatted_output.extend(self._format_contacts(data["response_data"]))
                elif "emailAddress" in data:  # Profile data
                    formatted_output.append(self._format_profile(data))
                else:
                    formatted_output.append("✅ Operation completed successfully")
            return "\n".join(formatted_output) if formatted_output else "✅ Operation completed successfully"
        except Exception as e:
            return f"Error formatting result: {str(e)}"

    def _format_emails(self, messages: list) -> list:
        """Format emails with basic details"""
        formatted = []
        if not messages:
            formatted.append("📭 No emails found matching your criteria.")
            return formatted
        formatted.append(f"📧 Found {len(messages)} email(s):")
        formatted.append("=" * 60)
        for i, msg in enumerate(messages, 1):
            subject = msg.get("subject", "No Subject")
            sender = msg.get("sender", "Unknown Sender")
            date = msg.get("messageTimestamp", "")
            preview = msg.get("preview", {}).get("body", "")
            if date:
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    formatted_date = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    formatted_date = date
            else:
                formatted_date = "Unknown Date"
            formatted.append(f"\n📨 Email #{i}")
            formatted.append(f"📋 Subject: {subject}")
            formatted.append(f"👤 From: {sender}")
            formatted.append(f"📅 Date: {formatted_date}")
            if preview:
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                formatted.append(f"📝 Preview: {preview}")
            formatted.append("-" * 40)
        return formatted

    def _format_emails_with_full_content(self, messages: list) -> str:
        """Format emails with full body content"""
        formatted = []
        if not messages:
            return "📭 No emails found matching your criteria."
        formatted.append(f"📧 Found {len(messages)} email(s):")
        formatted.append("=" * 60)
        for i, msg in enumerate(messages, 1):
            subject = msg.get("subject", "No Subject")
            sender = msg.get("sender", "Unknown Sender")
            date = msg.get("messageTimestamp", "")
            content = self._extract_email_content(msg)
            if date:
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    formatted_date = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    formatted_date = date
            else:
                formatted_date = "Unknown Date"
            formatted.append(f"\n📨 Email #{i}")
            formatted.append(f"📋 Subject: {subject}")
            formatted.append(f"👤 From: {sender}")
            formatted.append(f"📅 Date: {formatted_date}")
            if content:
                if len(content) > 1000:
                    content = content[:1000] + "..."
                formatted.append(f"📝 Full Content: {content}")
            else:
                formatted.append("📝 No content available")
            formatted.append("-" * 40)
        return "\n".join(formatted)

    def _extract_email_content(self, msg: dict) -> str:
        """Extract full email content (plain text or HTML)"""
        try:
            payload = msg.get("payload", {})
            parts = payload.get("parts", [])
            for part in parts:
                if part.get("mimeType") == "text/plain":
                    body_data = part.get("body", {}).get("data", "")
                    if body_data:
                        body_data = body_data.replace('-', '+').replace('_', '/')
                        while len(body_data) % 4:
                            body_data += '='
                        decoded = base64.b64decode(body_data).decode('utf-8', errors='ignore')
                        return self._clean_email_text(decoded)
                elif part.get("mimeType") == "text/html":
                    body_data = part.get("body", {}).get("data", "")
                    if body_data:
                        body_data = body_data.replace('-', '+').replace('_', '/')
                        while len(body_data) % 4:
                            body_data += '='
                        decoded = base64.b64decode(body_data).decode('utf-8', errors='ignore')
                        clean_text = re.sub('<[^<]+?>', '', decoded)
                        return self._clean_email_text(clean_text)
            body = payload.get("body", {})
            if body.get("data"):
                body_data = body["data"].replace('-', '+').replace('_', '/')
                while len(body_data) % 4:
                    body_data += '='
                decoded = base64.b64decode(body_data).decode('utf-8', errors='ignore')
                return self._clean_email_text(decoded)
            return ""
        except Exception:
            return ""

    def _clean_email_text(self, text: str) -> str:
        """Clean email text for readability"""
        if not text:
            return ""
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = re.sub(r' +', ' ', text)
        return text.strip()

    def _format_drafts(self, drafts: list) -> list:
        formatted = []
        if not drafts:
            formatted.append("📝 No drafts found.")
            return formatted
        formatted.append(f"📝 Found {len(drafts)} draft(s):")
        formatted.append("=" * 50)
        for i, draft in enumerate(drafts, 1):
            message = draft.get("message", {})
            subject = message.get("subject", "No Subject")
            date = message.get("messageTimestamp", "")
            preview = message.get("preview", {}).get("body", "")
            if date:
                try:
                    dt = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    formatted_date = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    formatted_date = date
            else:
                formatted_date = "Unknown Date"
            formatted.append(f"\n📝 Draft #{i}")
            formatted.append(f"📋 Subject: {subject}")
            formatted.append(f"📅 Created: {formatted_date}")
            if preview:
                if len(preview) > 150:
                    preview = preview[:150] + "..."
                formatted.append(f"📄 Content: {preview}")
            formatted.append("-" * 30)
        return formatted

    def _format_labels(self, labels: list) -> list:
        formatted = []
        if not labels:
            formatted.append("🏷️ No labels found.")
            return formatted
        formatted.append(f"🏷️ Found {len(labels)} label(s):")
        formatted.append("=" * 40)
        system_labels = [label for label in labels if label.get("type") == "system"]
        user_labels = [label for label in labels if label.get("type") != "system"]
        if system_labels:
            formatted.append("\n📋 System Labels:")
            for label in system_labels:
                formatted.append(f"  • {label.get('name', 'Unknown')}")
        if user_labels:
            formatted.append("\n👤 Your Labels:")
            for label in user_labels:
                formatted.append(f"  • {label.get('name', 'Unknown')}")
        return formatted

    def _format_contacts(self, response_data: dict) -> str:
        """Format contacts response into a readable string"""
        try:
            formatted = []
            if not response_data or "connections" not in response_data:
                return "👥 No contacts found."
            connections = response_data.get("connections", [])
            formatted.append(f"👥 Found {len(connections)} contact(s):")
            formatted.append("=" * 40)
            for i, person in enumerate(connections, 1):
                names = person.get("names", [])
                emails = person.get("emailAddresses", [])
                name = names[0].get("displayName", "Unknown") if names else "Unknown"
                email = emails[0].get("value", "No email") if emails else "No email"
                formatted.append(f"\n👤 Contact #{i}")
                formatted.append(f"📛 Name: {name}")
                formatted.append(f"📧 Email: {email}")
                formatted.append("-" * 30)
            return "\n".join(formatted)
        except Exception as e:
            return f"Error formatting contacts: {str(e)}"

    def _format_profile(self, profile_data: dict) -> str:
        """Format Gmail profile data"""
        try:
            if not profile_data:
                return "👤 No profile data found."
            email_address = profile_data.get("emailAddress", "Unknown")
            messages_total = profile_data.get("messagesTotal", 0)
            threads_total = profile_data.get("threadsTotal", 0)
            history_id = profile_data.get("historyId", "N/A")
            formatted = [
                "👤 Gmail Profile Information:",
                "=" * 40,
                f"📧 Email Address: {email_address}",
                f"📬 Total Messages: {messages_total}",
                f"🧵 Total Threads: {threads_total}",
                f"🕰️ History ID: {history_id}"
            ]
            return "\n".join(formatted)
        except Exception as e:
            return f"Error formatting profile: {str(e)}"

    def compose_email_with_ai(self, recipient: str, subject: str = "", content: str = "", context: str = "") -> Dict[str, str]:
        try:
            system_prompt = """You are a professional email writing assistant. Compose clear, professional emails based on user input.
            Guidelines:
            - Use a professional but friendly tone
            - Include proper greeting and closing
            - Make the email clear and concise
            - Adapt formality based on recipient and context
            - If no subject is provided, create an appropriate one
            - Expand minimal content professionally
            Return JSON: {"subject": "Subject line", "body": "Email body"}"""
            user_prompt = f"""Compose an email for:
            - Recipient: {recipient}
            - Subject: {subject or 'Please create an appropriate subject'}
            - Content/Context: {content or context}
            - Additional context: {context if context != content else ''}"""
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Error composing email: {e}")
            return {
                "subject": subject or "Message",
                "body": content or "Hello,\n\nI hope this email finds you well.\n\nBest regards"
            }

    def refine_response_with_gpt(self, raw_response: str) -> str:
        """Refine raw result with GPT for clean format"""
        try:
            system_prompt = """You are a response refiner. Take the raw output from a Gmail operation and format it into a user-friendly response:
            - Use bullet points, headers, and emojis for readability.
            - Summarize long content, keeping key details.
            - Maintain a professional, concise tone.
            - Example: For profile, list email, message count, thread count; for labels, confirm creation.
            Return the refined response as a string."""
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": raw_response}
                ],
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Error refining response: {e}")
            return raw_response

    def analyze_user_intent(self, message: str) -> Dict[str, Any]:
        """Analyze user intent with improved prompt for better confidence"""
        try:
            system_prompt = """You are an intent analyzer for Gmail operations. Analyze the user query to determine the intent from the following actions:
            - AUTH: For queries like 'authenticate gmail', 'connect gmail', 'login to gmail'
            - LIST: List emails, drafts, labels, threads (e.g., 'show emails', 'list drafts')
            - SEARCH: Search emails or people, including spam (e.g., 'find emails from john', 'fetch spam mail')
            - SEND: Send an email (e.g., 'send email to john@example.com')
            - REPLY: Reply to an email/thread (e.g., 'reply to latest email')
            - MARK_READ: Mark emails as read (e.g., 'mark email as read')
            - MARK_UNREAD: Mark emails as unread (e.g., 'mark email as unread')
            - DELETE: Delete emails or drafts, including spam (e.g., 'delete spam emails')
            - MOVE_TO_TRASH: Move emails to trash (e.g., 'move emails from john to trash')
            - READ: Read emails with full content (e.g., 'read emails from john@example.com')
            - GET_PROFILE: Get Gmail profile info (e.g., 'get my profile', 'show profile')
            - CREATE_DRAFT: Create a draft email
            - SEND_DRAFT: Send an existing draft
            - CREATE_LABEL: Create a new label (e.g., 'create a label called fetch.ai')
            - ADD_LABEL: Add labels to emails
            - REMOVE_LABEL: Remove labels from emails
            - MODIFY_THREAD_LABELS: Modify thread labels
            - PATCH_LABEL: Update label properties
            - GET_CONTACTS: Get contact list (e.g., 'list my contacts')
            - SEARCH_PEOPLE: Search for people
            - GET_ATTACHMENT: Download email attachments
            - LIST_THREADS: List email threads
            For SEND, extract recipient, subject, content, context. For CREATE_LABEL, extract label_name. For SEARCH/DELETE/READ/MOVE_TO_TRASH, include 'is_spam: true' for spam-related queries or 'sender' for email-specific queries. For others, extract relevant parameters (e.g., query, email_id, label_id).
            Boost confidence (0.8-1.0) for clear matches. If ambiguous, pick best fit but note clarification needed.
            Return JSON: {"intent": "ACTION_NAME", "parameters": {...}, "confidence": float}"""
            response = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Error analyzing intent: {e}")
            return {"intent": "UNKNOWN", "parameters": {}, "confidence": 0.0}

# Initialize GmailAgent without auth
gmail_agent = GmailAgent(
    auth_config_id=os.getenv("GMAIL_AUTH_CONFIG_ID")
)

@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    """Handle incoming messages from AgentVerse via uAgent"""
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id),
    )
    text = ""
    for item in msg.content:
        if isinstance(item, TextContent):
            text += item.text

    print(f"📥 Received query from AgentVerse: {text}")

    # Check for auth completion if pending
    if gmail_agent.connection_request and not gmail_agent.is_authenticated():
        if gmail_agent.complete_auth():
            response = "✅ Gmail authentication completed successfully! Try commands like 'Create a label called fetch.ai' or 'Get my profile'."
        else:
            response = "⏳ Authentication not yet completed. Please complete the auth flow in your browser and try again."
    else:
        # Process query
        try:
            result = gmail_agent.process_query(text)
            if result.get("success"):
                response = result.get("formatted_result", "Action completed successfully")
            else:
                response = f"❌ Error: {result.get('error', 'Unknown error')}"
        except Exception as e:
            response = f"❌ Sorry, couldn’t process that: {str(e)}"

    print(f"📤 Sending response to AgentVerse: {response[:100]}...")
    await ctx.send(
        sender,
        ChatMessage(
            timestamp=datetime.now(UTC),
            msg_id=uuid4(),
            content=[
                TextContent(type="text", text=response),
            ]
        )
    )

@protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    """Handle acknowledgment from AgentVerse"""
    pass

agent.include(protocol, publish_manifest=True)

if __name__ == "__main__":
    print("🤖 Starting Gmail-ASI-Agent...")
    try:
        agent.run()
    except KeyboardInterrupt:
        print("🛑 Agent stopped gracefully. Goodbye!")
