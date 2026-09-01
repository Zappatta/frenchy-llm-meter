"""The probe's value is the ordering of its stages, so that is what is tested.

Nothing here needs a radio. The failure mode worth catching is a bleak upgrade
rewording a debug message, which silently demotes every diagnosis to an earlier
stage and turns a GATT fault into a phantom range problem.
"""

from frenchy_llm_meter.probe import furthest_stage, peer_dropped

# Trimmed from a real failing run, 2026-08-22: the link that connected, walked
# the service definition, and died during characteristic enumeration.
DIED_AT_CHARACTERISTICS = [
    "Connecting to BLE device @ 3745215F-C275-4E80-E9E5-31DC4A2770AB",
    "centralManager_didConnectPeripheral_",
    "Retrieving services...",
    "peripheral_didDiscoverServices_",
    "Services discovered",
    "Retrieving characteristics for service 6B1D0001-9A3F-4C6E-B0D2-7F2A5C8E41AA",
    "centralManager_didDisconnectPeripheral_error_",
    "Peripheral Device disconnected!",
]


def test_reports_the_furthest_stage_not_the_last_line():
    # The last line is a disconnect. The diagnosis is the last stage REACHED.
    assert furthest_stage(DIED_AT_CHARACTERISTICS) == "retrieving_characteristics"


def test_detects_that_the_peer_closed_the_link():
    assert peer_dropped(DIED_AT_CHARACTERISTICS) is True


def test_a_connect_that_never_landed():
    lines = ["Connecting to BLE device @ AA:BB"]
    assert furthest_stage(lines) == "connecting"
    assert peer_dropped(lines) is False


def test_no_output_at_all():
    assert furthest_stage([]) == "nothing"
    assert peer_dropped([]) is False


def test_stages_are_ordered_worst_to_best():
    # Ordering is the whole mechanism: furthest_stage walks the list and keeps
    # the last match, so a list in the wrong order reports nonsense.
    from frenchy_llm_meter.probe import STAGES

    names = [name for name, _ in STAGES]
    assert names == [
        "connecting",
        "connected",
        "retrieving_services",
        "services_discovered",
        "retrieving_characteristics",
    ]


def test_the_docstring_names_the_real_stages():
    """The stage names are the diagnosis, so they are also what people grep for.

    The first version of this module documented five stages that existed
    nowhere in the code — only two of the names matched. The printed output was
    correct, so nothing failed; a grep for a documented name simply returned
    nothing and read as a broken tool.
    """
    from frenchy_llm_meter import probe

    assert probe.__doc__ is not None
    for name, _ in probe.STAGES:
        assert name in probe.__doc__, f"stage {name!r} is not in the module docstring"
