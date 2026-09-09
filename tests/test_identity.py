from housekeeper.identity import classify, merge_records, unwrap_env
from housekeeper.models import Action, Source

APP_ID = "abcdefghijklmnopabcdefghijklmnop"


def test_pwa_is_not_browser_package(entry):
    app = classify(
        entry(
            (
                "/opt/google/chrome/google-chrome",
                "--profile-directory=Default",
                "--app-id=" + APP_ID,
            )
        )
    )
    assert app.provider == "chrome"
    assert app.action == Action.CHROME
    assert app.identity == APP_ID
    assert app.management[-1] == "chrome://apps"
    assert not any("--app-id" in arg for arg in app.management)


def test_pwa_profiles_and_user_data_directories_are_distinct(entry):
    records = [
        classify(
            entry(
                (
                    "/usr/bin/chromium",
                    "--app-id=" + APP_ID,
                    "--profile-directory=" + profile,
                    "--user-data-dir=" + root,
                )
            )
        )
        for profile, root in [("Default", "/a"), ("Profile 1", "/a"), ("Default", "/b")]
    ]
    assert len(merge_records(records)) == 3


def test_unrecognized_browser_flags_are_not_forwarded(entry):
    app = classify(
        entry(
            (
                "/usr/bin/chromium",
                "--app-id=" + APP_ID,
                "--load-extension=/tmp/untrusted",
                "--disable-web-security",
            )
        )
    )
    assert app.management == ("/usr/bin/chromium", "chrome://apps")


def test_browser_itself_remains_native_candidate(entry):
    assert classify(entry(("/usr/bin/google-chrome-stable", "%U"))).source == Source.OTHER


def test_steam_game_is_not_steam_client(entry):
    app = classify(entry(("/usr/bin/steam", "steam://rungameid/123")))
    assert app.provider == "steam" and app.identity == "123"
    assert classify(entry(("/usr/bin/steam", "%U"))).source == Source.OTHER


def test_firefoxpwa_is_distinct(entry):
    app = classify(entry(("/usr/bin/firefoxpwa", "site", "launch", "TESTSITE", "--protocol", "%u")))
    assert app.provider == "firefoxpwa" and app.action == Action.INSTRUCTIONS


def test_shell_wrapper_is_not_unwrapped(entry):
    app = classify(entry(("/usr/bin/sh", "-c", "/home/example/app.AppImage")))
    assert app.source == Source.OTHER
    assert unwrap_env(("env", "--unset=PATH", "example")) == ("example",)


def test_simple_env_assignments_are_unwrapped(entry):
    app = classify(entry(("env", "LANG=en_US.UTF-8", "steam", "steam://rungameid/42")))
    assert app.provider == "steam"


def test_identical_names_do_not_merge(entry):
    apps = [classify(entry(desktop_id=f"app{i}.desktop")) for i in range(2)]
    assert len(merge_records(apps)) == 2


def test_identical_installation_collects_entry_paths(entry):
    command = ("/usr/bin/chromium", "--app-id=" + APP_ID)
    apps = [classify(entry(command, desktop_id=f"app{i}.desktop")) for i in range(2)]
    merged = merge_records(apps)
    assert len(merged) == 1 and len(merged[0].entries) == 2
