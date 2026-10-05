from stackos_connectors.connectors.telegram.payloads import message_content, send_options


def test_send_options_do_not_override_caller_execution_choices():
    explicit = {
        "from_background": False,
        "only_preview": True,
        "update_order_of_installed_sticker_sets": True,
        "scheduling_state": {"@type": "messageSchedulingStateSendAtDate", "send_date": 12345},
        "suggested_post_info": {"@type": "inputSuggestedPostInfo", "send_date": 23456},
    }
    assert send_options(explicit, sending_id=17) == {
        "@type": "messageSendOptions",
        "sending_id": 17,
        **explicit,
    }
    assert send_options({}, sending_id=18) == {"@type": "messageSendOptions", "sending_id": 18}


def test_location_and_contact_preserve_native_fields():
    assert message_content(
        {
            "kind": "location",
            "latitude": 12.5,
            "longitude": 10.0,
            "horizontal_accuracy": 3.0,
            "live_period": 0,
        }
    ) == {
        "@type": "inputMessageLocation",
        "location": {
            "@type": "location",
            "latitude": 12.5,
            "longitude": 10.0,
            "horizontal_accuracy": 3.0,
        },
    }
    assert message_content(
        {
            "kind": "contact",
            "phone_number": "+12345",
            "first_name": "First",
            "last_name": "Last",
            "vcard": "caller value",
            "user_id": 77,
        }
    ) == {
        "@type": "inputMessageContact",
        "contact": {
            "@type": "contact",
            "phone_number": "+12345",
            "first_name": "First",
            "last_name": "Last",
            "vcard": "caller value",
            "user_id": 77,
        },
    }


def test_live_location_keeps_native_wrapper_and_caller_duration():
    assert message_content(
        {
            "kind": "location",
            "latitude": 12.5,
            "longitude": 10.0,
            "horizontal_accuracy": 3.0,
            "live_period": 600,
            "heading": 90,
            "proximity_alert_radius": 100,
        }
    ) == {
        "@type": "inputMessageLiveLocation",
        "location": {
            "@type": "liveLocation",
            "location": {
                "@type": "location",
                "latitude": 12.5,
                "longitude": 10.0,
                "horizontal_accuracy": 3.0,
            },
            "live_period": 600,
            "heading": 90,
            "proximity_alert_radius": 100,
        },
    }
