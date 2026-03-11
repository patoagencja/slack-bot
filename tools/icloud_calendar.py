"""iCloud Calendar integration via CalDAV."""
import os
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

try:
    import caldav
    from caldav.elements import dav
    import vobject
    _CALDAV_AVAILABLE = True
except ImportError:
    _CALDAV_AVAILABLE = False
    logger.warning("caldav lub vobject nie jest zainstalowany — kalendarz iCloud niedostępny")

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"
ICLOUD_USERNAME   = os.environ.get("ICLOUD_USERNAME")   # np. jan@icloud.com
ICLOUD_PASSWORD   = os.environ.get("ICLOUD_APP_PASSWORD")  # app-specific password


def _get_client():
    if not _CALDAV_AVAILABLE:
        raise RuntimeError("Biblioteka caldav nie jest zainstalowana.")
    if not ICLOUD_USERNAME or not ICLOUD_PASSWORD:
        raise RuntimeError("Brak ICLOUD_USERNAME lub ICLOUD_APP_PASSWORD w zmiennych środowiskowych.")
    return caldav.DAVClient(
        url=ICLOUD_CALDAV_URL,
        username=ICLOUD_USERNAME,
        password=ICLOUD_PASSWORD,
    )


def _parse_event(vevent):
    """Wyciąga podstawowe pola z vobject VEVENT."""
    summary = str(vevent.summary.value) if hasattr(vevent, "summary") else "(brak tytułu)"
    location = str(vevent.location.value) if hasattr(vevent, "location") else None
    description = str(vevent.description.value) if hasattr(vevent, "description") else None

    dtstart = vevent.dtstart.value if hasattr(vevent, "dtstart") else None
    dtend   = vevent.dtend.value   if hasattr(vevent, "dtend")   else None

    # Normalize to datetime
    if isinstance(dtstart, datetime):
        start_str = dtstart.strftime("%Y-%m-%d %H:%M")
    elif dtstart:
        start_str = str(dtstart)
    else:
        start_str = None

    if isinstance(dtend, datetime):
        end_str = dtend.strftime("%Y-%m-%d %H:%M")
    elif dtend:
        end_str = str(dtend)
    else:
        end_str = None

    result = {"title": summary, "start": start_str, "end": end_str}
    if location:
        result["location"] = location
    if description:
        result["description"] = description[:200]
    return result


def icloud_calendar_tool(
    action: str = "list",
    date_from: str = None,
    date_to: str = None,
    title: str = None,
    start: str = None,
    end: str = None,
    location: str = None,
    description: str = None,
    calendar_name: str = None,
):
    """
    Zarządza kalendarzem iCloud przez CalDAV.

    action:
      - "list"   — lista wydarzeń w podanym zakresie dat
      - "create" — tworzy nowe wydarzenie
    """
    try:
        client = _get_client()
        principal = client.principal()
        calendars = principal.calendars()

        if not calendars:
            return {"error": "Brak kalendarzy w koncie iCloud."}

        # Wybierz kalendarz po nazwie lub pierwszy dostępny
        calendar = None
        if calendar_name:
            for cal in calendars:
                props = cal.get_properties([dav.DisplayName()])
                name = props.get("{DAV:}displayname", "")
                if calendar_name.lower() in name.lower():
                    calendar = cal
                    break
        if calendar is None:
            calendar = calendars[0]

        # Pobierz nazwę wybranego kalendarza
        try:
            cal_props = calendar.get_properties([dav.DisplayName()])
            cal_display_name = cal_props.get("{DAV:}displayname", "Kalendarz")
        except Exception:
            cal_display_name = "Kalendarz"

        if action == "list":
            # Domyślny zakres: dzisiaj + 7 dni
            now = datetime.now()
            if date_from:
                try:
                    start_dt = datetime.strptime(date_from, "%Y-%m-%d")
                except ValueError:
                    start_dt = now
            else:
                start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)

            if date_to:
                try:
                    end_dt = datetime.strptime(date_to, "%Y-%m-%d").replace(
                        hour=23, minute=59, second=59
                    )
                except ValueError:
                    end_dt = start_dt + timedelta(days=7)
            else:
                end_dt = start_dt + timedelta(days=7)

            # Szukaj we wszystkich kalendarzach gdy nie podano konkretnej nazwy
            cals_to_search = [calendar] if calendar_name else calendars
            events = []
            cal_names_used = []
            for cal in cals_to_search:
                try:
                    events_raw = cal.date_search(start=start_dt, end=end_dt, expand=True)
                    for ev in events_raw:
                        try:
                            cal_obj = vobject.readOne(ev.data)
                            if hasattr(cal_obj, "vevent"):
                                events.append(_parse_event(cal_obj.vevent))
                        except Exception as parse_err:
                            logger.warning(f"Błąd parsowania wydarzenia: {parse_err}")
                    try:
                        props = cal.get_properties([dav.DisplayName()])
                        cal_names_used.append(props.get("{DAV:}displayname", "?"))
                    except Exception:
                        pass
                except Exception as cal_err:
                    logger.warning(f"Błąd przeszukiwania kalendarza: {cal_err}")

            # Sortuj po dacie startu
            events.sort(key=lambda e: e.get("start") or "")

            return {
                "calendar": ", ".join(cal_names_used) if cal_names_used else cal_display_name,
                "date_from": start_dt.strftime("%Y-%m-%d"),
                "date_to": end_dt.strftime("%Y-%m-%d"),
                "count": len(events),
                "events": events,
            }

        elif action == "create":
            if not title or not start:
                return {"error": "Aby stworzyć wydarzenie potrzebne są: title i start (format: YYYY-MM-DD HH:MM)."}

            try:
                start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
            except ValueError:
                return {"error": f"Nieprawidłowy format daty startu: '{start}'. Użyj YYYY-MM-DD HH:MM."}

            if end:
                try:
                    end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
                except ValueError:
                    end_dt = start_dt + timedelta(hours=1)
            else:
                end_dt = start_dt + timedelta(hours=1)

            uid = start_dt.strftime("%Y%m%dT%H%M%S") + "-sebol@pato"
            ical_lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Sebol//Pato Bot//PL",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"SUMMARY:{title}",
                f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}",
            ]
            if location:
                ical_lines.append(f"LOCATION:{location}")
            if description:
                ical_lines.append(f"DESCRIPTION:{description}")
            ical_lines += ["END:VEVENT", "END:VCALENDAR"]
            ical_data = "\r\n".join(ical_lines)

            calendar.save_event(ical_data)

            return {
                "status": "created",
                "calendar": cal_display_name,
                "title": title,
                "start": start_dt.strftime("%Y-%m-%d %H:%M"),
                "end": end_dt.strftime("%Y-%m-%d %H:%M"),
                "location": location,
            }

        else:
            return {"error": f"Nieznana akcja: '{action}'. Dostępne: 'list', 'create'."}

    except Exception as e:
        logger.error(f"Błąd iCloud Calendar: {e}", exc_info=True)
        return {"error": f"Błąd kalendarza iCloud: {e}"}
