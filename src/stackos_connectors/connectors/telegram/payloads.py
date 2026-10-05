"""Pure native content encoding from caller-prepared files and formatted text.

Protocol source: https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl
"""

import base64
from typing import Any

from stackos_connectors.errors import ValidationError


def reply_markup(rows: list[list[dict[str, Any]]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return {
        "@type": "replyMarkupInlineKeyboard",
        "rows": [
            [
                {
                    "@type": "inlineKeyboardButton",
                    "text": button["text"],
                    "style": {
                        "@type": {
                            "default": "buttonStyleDefault",
                            "primary": "buttonStylePrimary",
                            "danger": "buttonStyleDanger",
                            "success": "buttonStyleSuccess",
                            "link": "buttonStyleLink",
                        }[button["style"]]
                    },
                    "type": {"@type": "inlineKeyboardButtonTypeUrl", "url": button["url"]}
                    if button["url"] is not None
                    else {
                        "@type": "inlineKeyboardButtonTypeCallback",
                        "data": base64.b64encode((button["callback_data"] or "").encode()).decode(),
                    },
                }
                for button in row
            ]
            for row in rows
        ],
    }


def topic(topic: dict[str, Any] | None) -> dict[str, Any] | None:
    if topic is None:
        return None
    kind, field = {
        "thread": ("messageTopicThread", "message_thread_id"),
        "forum": ("messageTopicForum", "forum_topic_id"),
        "direct_messages": ("messageTopicDirectMessages", "direct_messages_chat_topic_id"),
        "saved_messages": ("messageTopicSavedMessages", "saved_messages_topic_id"),
    }[topic["kind"]]
    return {"@type": kind, field: topic["id"]}


def send_options(options: dict[str, Any], *, sending_id: int) -> dict[str, Any]:
    return {
        **options,
        "@type": "messageSendOptions",
        "sending_id": sending_id,
    }


def message_content(content: dict[str, Any]) -> dict[str, Any]:
    if content["kind"] == "text":
        return {
            "@type": "inputMessageText",
            "text": content["text"],
            "link_preview_options": {
                "@type": "linkPreviewOptions",
                "is_disabled": content["disable_link_preview"],
                "url": "",
                "force_small_media": False,
                "force_large_media": False,
                "show_above_text": False,
            },
            "clear_draft": False,
        }
    if content["kind"] == "photo":
        photo: dict[str, Any] = {
            "@type": "inputPhoto",
            "photo": content["file"],
            "thumbnail": _input_thumbnail(content["thumbnail"]),
            "video": None,
            "added_sticker_file_ids": [],
            "width": content["width"],
            "height": content["height"],
        }
        return {
            "@type": "inputMessagePhoto",
            "photo": photo,
            "caption": content["caption"],
            "show_caption_above_media": content["show_caption_above_media"],
            "self_destruct_type": None,
            "has_spoiler": content["has_spoiler"],
        }
    if content["kind"] == "video":
        video: dict[str, Any] = {
            "@type": "inputVideo",
            "video": content["file"],
            "thumbnail": _input_thumbnail(content["thumbnail"]),
            "cover": content["cover"] if content["cover"] else None,
            "start_timestamp": content["start_timestamp"],
            "added_sticker_file_ids": [],
            "duration": content["duration"],
            "width": content["width"],
            "height": content["height"],
            "supports_streaming": content["supports_streaming"],
        }
        return {
            "@type": "inputMessageVideo",
            "video": video,
            "caption": content["caption"],
            "show_caption_above_media": content["show_caption_above_media"],
            "self_destruct_type": None,
            "has_spoiler": content["has_spoiler"],
        }
    if content["kind"] == "animation":
        animation: dict[str, Any] = {
            "@type": "inputAnimation",
            "animation": content["file"],
            "thumbnail": _input_thumbnail(content["thumbnail"]),
            "added_sticker_file_ids": [],
            "duration": content["duration"],
            "width": content["width"],
            "height": content["height"],
        }
        return {
            "@type": "inputMessageAnimation",
            "animation": animation,
            "caption": content["caption"],
            "show_caption_above_media": content["show_caption_above_media"],
            "has_spoiler": content["has_spoiler"],
        }
    if content["kind"] == "audio":
        audio: dict[str, Any] = {
            "@type": "inputAudio",
            "audio": content["file"],
            "album_cover_thumbnail": _input_thumbnail(content["thumbnail"]),
            "duration": content["duration"],
            "title": content["title"],
            "performer": content["performer"],
        }
        return {"@type": "inputMessageAudio", "audio": audio, "caption": content["caption"]}
    if content["kind"] == "voice":
        voice_note: dict[str, Any] = {
            "@type": "inputVoiceNote",
            "voice_note": content["file"],
            "duration": content["duration"],
            "waveform": content["waveform"],
        }
        return {
            "@type": "inputMessageVoiceNote",
            "voice_note": voice_note,
            "caption": content["caption"],
            "self_destruct_type": None,
        }
    if content["kind"] == "video_note":
        video_note: dict[str, Any] = {
            "@type": "inputVideoNote",
            "video_note": content["file"],
            "thumbnail": _input_thumbnail(content["thumbnail"]),
            "duration": content["duration"],
            "length": content["length"],
        }
        return {
            "@type": "inputMessageVideoNote",
            "video_note": video_note,
            "self_destruct_type": None,
        }
    if content["kind"] == "document":
        document: dict[str, Any] = {
            "@type": "inputDocument",
            "document": content["file"],
            "thumbnail": _input_thumbnail(content["thumbnail"]),
            "disable_content_type_detection": content["disable_content_type_detection"],
        }
        return {
            "@type": "inputMessageDocument",
            "document": document,
            "caption": content["caption"],
        }
    if content["kind"] == "sticker":
        sticker: dict[str, Any] = {
            "@type": "inputSticker",
            "sticker": content["file"],
            "thumbnail": _input_thumbnail(content["thumbnail"]),
            "width": content["width"],
            "height": content["height"],
        }
        return {"@type": "inputMessageSticker", "sticker": sticker, "emoji": content["emoji"]}
    if content["kind"] == "poll":
        poll_type = (
            {
                "@type": "inputPollTypeQuiz",
                "correct_option_ids": content["correct_option_ids"],
                "explanation": content["explanation"],
                "explanation_media": None,
            }
            if content["quiz"]
            else {"@type": "inputPollTypeRegular", "allow_adding_options": False}
        )
        return {
            "@type": "inputMessagePoll",
            "question": content["question"],
            "options": [
                {"@type": "inputPollOption", "text": value, "media": None}
                for value in content["options"]
            ],
            "description": {"@type": "formattedText", "text": "", "entities": []},
            "media": None,
            "is_anonymous": content["is_anonymous"],
            "allows_multiple_answers": content["allows_multiple_answers"],
            "allows_revoting": content["allows_revoting"],
            "members_only": False,
            "country_codes": [],
            "shuffle_options": False,
            "hide_results_until_closes": False,
            "type": poll_type,
            "open_period": content["open_period"],
            "close_date": content["close_date"],
            "is_closed": content["is_closed"],
        }
    if content["kind"] == "location":
        location = {
            "@type": "location",
            "latitude": content["latitude"],
            "longitude": content["longitude"],
            "horizontal_accuracy": content.get("horizontal_accuracy", 0),
        }
        if content["live_period"]:
            return {
                "@type": "inputMessageLiveLocation",
                "location": {
                    "@type": "liveLocation",
                    "location": location,
                    "live_period": content["live_period"],
                    "heading": content["heading"],
                    "proximity_alert_radius": content["proximity_alert_radius"],
                },
            }
        return {"@type": "inputMessageLocation", "location": location}
    if content["kind"] == "venue":
        return {
            "@type": "inputMessageVenue",
            "venue": {
                "@type": "venue",
                "location": {
                    "@type": "location",
                    "latitude": content["latitude"],
                    "longitude": content["longitude"],
                    "horizontal_accuracy": 0,
                },
                "title": content["title"],
                "address": content["address"],
                "provider": content["provider"],
                "id": content["venue_id"],
                "type": content["venue_type"],
            },
        }
    if content["kind"] == "contact":
        return {
            "@type": "inputMessageContact",
            "contact": {
                "@type": "contact",
                **{key: value for key, value in content.items() if key != "kind"},
            },
        }
    if content["kind"] == "dice":
        return {"@type": "inputMessageDice", "emoji": content["emoji"], "clear_draft": False}
    raise ValidationError("unsupported Telegram message content")


def _input_thumbnail(file: dict[str, Any] | None) -> dict[str, Any] | None:
    if file is None:
        return None
    return {"@type": "inputThumbnail", "thumbnail": file, "width": 0, "height": 0}
