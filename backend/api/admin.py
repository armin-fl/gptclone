from django.contrib import admin

from .models import Conversation, KnowledgeChunk, KnowledgeDocument, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ("role", "content", "created_at")
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "updated_at", "created_at")
    search_fields = ("title", "user__phone_number")
    list_filter = ("created_at", "updated_at")
    inlines = [MessageInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "conversation", "role", "created_at")
    search_fields = ("content", "conversation__title", "conversation__user__phone_number")
    list_filter = ("role", "created_at")


class KnowledgeChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    extra = 0
    readonly_fields = ("id", "chunk_index", "content", "metadata", "created_at")
    can_delete = False


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "chunk_count", "updated_at", "created_at")
    search_fields = ("title", "source_name", "user__phone_number")
    list_filter = ("created_at", "updated_at")
    readonly_fields = ("content_hash", "chunk_count", "created_at", "updated_at")
    inlines = [KnowledgeChunkInline]


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "chunk_index", "created_at")
    search_fields = ("content", "document__title", "document__user__phone_number")
