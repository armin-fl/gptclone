from django.urls import path

from .views import (
    ConversationDetailView,
    ConversationEditMessageView,
    ConversationForkMessageView,
    ConversationListCreateView,
    ConversationRegenerateMessageView,
    ConversationSendMessageView,
    ImageGenerationView,
    ImageModelListView,
    LlmModelListView,
)

urlpatterns = [
    path("models/", LlmModelListView.as_view(), name="llm-model-list"),
    path("image-models/", ImageModelListView.as_view(), name="image-model-list"),
    path("images/generations/", ImageGenerationView.as_view(), name="image-generation"),
    path("conversations/", ConversationListCreateView.as_view(), name="conversation-list-create"),
    path("conversations/<uuid:conversation_id>/", ConversationDetailView.as_view(), name="conversation-detail"),
    path(
        "conversations/<uuid:conversation_id>/messages/",
        ConversationSendMessageView.as_view(),
        name="conversation-send-message",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages/<int:message_id>/regenerate/",
        ConversationRegenerateMessageView.as_view(),
        name="conversation-regenerate-message",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages/<int:message_id>/fork/",
        ConversationForkMessageView.as_view(),
        name="conversation-fork-message",
    ),
    path(
        "conversations/<uuid:conversation_id>/messages/<int:message_id>/edit/",
        ConversationEditMessageView.as_view(),
        name="conversation-edit-message",
    ),
]
