"""Immutable fixed Telegram native method names; route overrides are not accepted."""

from types import MappingProxyType

METHOD_ACTIONS = MappingProxyType(
    {
        "getMe": "telegram.identity.get",
        "getUser": "telegram.user.get",
        "getChat": "telegram.chat.get",
        "searchPublicChat": "telegram.chat.searchPublic",
        "createPrivateChat": "telegram.chat.createPrivate",
        "getBasicGroup": "telegram.group.get",
        "getSupergroup": "telegram.supergroup.get",
        "getChatAvailableMessageSenders": "telegram.chat.senders",
        "setChatMessageSender": "telegram.chat.sender.set",
        "getMessage": "telegram.message.get",
        "getChats": "telegram.chats.get",
        "loadChats": "telegram.chats.load",
        "getChatHistory": "telegram.history.get",
        "parseTextEntities": "telegram.text.parse",
        "answerCallbackQuery": "telegram.callback.answer",
        "sendMessage": "telegram.message.send",
        "sendMessageAlbum": "telegram.album.send",
        "forwardMessages": "telegram.message.forward",
        "deleteMessages": "telegram.message.delete",
        "setMessageReactions": "telegram.message.react",
        "stopPoll": "telegram.poll.stop",
        "editMessageText": "telegram.message.editText",
        "editMessageMedia": "telegram.message.editMedia",
        "editMessageCaption": "telegram.message.editCaption",
        "editMessageReplyMarkup": "telegram.message.editButtons",
        "editMessageLiveLocation": "telegram.message.editLocation",
        "downloadFile": "telegram.file.download",
    }
)
