/**
 * Zuordnungs-Stufe des zweistufigen Protokoll-Workflows.
 *
 * Stufe 1: Sven ordnet Termin und Asana-Board zu, prueft die Sprecher in Plaud
 * und gibt frei. Erst danach zieht Mara das Transkript. Vorher war die
 * Reihenfolge umgekehrt, wodurch jede Sprecherkorrektur zu spaet kam.
 *
 * Bewusst eigenstaendig statt Erweiterung von review.js: der Review-Editor ist
 * die taeglich genutzte Strecke und hat keinerlei Test-Abdeckung im Frontend.
 * Die Dropdown-Logik ist deshalb dupliziert statt geteilt. Wenn die Vorauswahl
 * hier angepasst wird, gehoert dieselbe Aenderung nach review.js.
 */
(function () {
    'use strict';

    var ctx = window.ASSIGN_CONTEXT || {};
    var tokenParam = 'token=' + encodeURIComponent(ctx.token || '');

    var eventSelect   = document.getElementById('event-select');
    var boardSelect   = document.getElementById('board-select');
    var sectionSelect = document.getElementById('section-select');
    var boardField    = document.getElementById('board-field');
    var sectionField  = document.getElementById('section-field');
    var asanaCheckbox = document.getElementById('asana-checkbox');
    var assignBtn     = document.getElementById('assign-btn');
    var discardBtn    = document.getElementById('discard-btn');
    var speakerList   = document.getElementById('speaker-list');
    var speakerReload = document.getElementById('speaker-reload');
    var assignInfo    = document.getElementById('assign-info');
    var attendeeHint  = document.getElementById('attendee-hint');

    // Termine gecacht, weil beim Absenden die Eingeladenen und die Termindaten
    // des gewaehlten Eintrags mitgeschickt werden.
    var eventsById = {};

    // ------------------------------------------------------------------
    // Helfer
    // ------------------------------------------------------------------

    /**
     * Meldet den Abschluss und kehrt zur Uebersicht zurueck, sofern der
     * Vorgang von dort aus geoeffnet wurde.
     *
     * Beim Abarbeiten mehrerer Aufnahmen am Stueck ist die Sackgasse nach dem
     * Absenden das groesste Aergernis: man muss jedes Mal zurueckblaettern.
     * Die kurze Pause ist Absicht — ohne sie bliebe unklar, ob es geklappt hat.
     */
    function fertigMelden(titel, text) {
        var parameter = new URLSearchParams(window.location.search);
        var zurueck = parameter.get('weiter') === 'uebersicht';
        // Den Reiter mitnehmen, aus dem der Vorgang geöffnet wurde — sonst
        // landet man wieder beim ersten und braucht einen Klick zurück.
        var reiter = parameter.get('reiter');
        var ziel = '/protokolle' + (reiter ? '#' + encodeURIComponent(reiter) : '');
        document.querySelector('.hg-main').innerHTML =
            '<section class="hg-recording-card">' +
            '<div class="hg-recording-title">' + titel + '</div>' +
            '<div class="hg-recording-meta">' + text +
            (zurueck ? '<br><br>Zurück zur Übersicht …' : '') +
            '</div>' +
            (zurueck ? '' :
                '<div class="hg-recording-meta" style="margin-top:14px">' +
                '<a href="' + ziel + '">Zur Übersicht aller Protokolle</a></div>') +
            '</section>';
        if (zurueck) {
            window.setTimeout(function () {
                window.location.href = ziel;
            }, 1200);
        }
    }

    /**
     * Zeit eines Outlook-Termins in Ortszeit.
     *
     * Microsoft Graph liefert UTC, kennzeichnet das aber nur im separaten
     * Feld `timeZone` — der Zeitstempel selbst trägt keinen Offset
     * ("2026-10-19T07:00:00.0000000"). JavaScript liest einen solchen Wert
     * als Ortszeit und zeigt im Sommer zwei Stunden zu früh an.
     *
     * Die Sekundenbruchteile werden abgeschnitten: Sieben Nachkommastellen
     * sind nicht ISO-8601, manche Browser lehnen den Wert ab.
     */
    function ortszeit(utcIso) {
        var roh = utcIso || '';
        var d = new Date(roh.slice(0, 19) + 'Z');
        if (isNaN(d.getTime())) {
            return roh.slice(11, 16);   // besser die Rohzeit als gar nichts
        }
        return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
    }

    function fillSelect(select, items, placeholder) {
        select.innerHTML = '';
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = placeholder;
        select.appendChild(opt);
        items.forEach(function (item) {
            var o = document.createElement('option');
            o.value = item.value;
            o.textContent = item.label;
            select.appendChild(o);
        });
    }

    function normalize(s) {
        return (s || '').toLowerCase().replace(/[^a-zä-ü0-9 ]/gi, ' ')
            .replace(/\s+/g, ' ').trim();
    }

    function titleSimilarity(a, b) {
        var wa = normalize(a).split(' ').filter(Boolean);
        var wb = normalize(b).split(' ').filter(Boolean);
        if (!wa.length || !wb.length) return 0;
        var common = wa.filter(function (w) { return wb.indexOf(w) !== -1; });
        return common.length / Math.max(wa.length, wb.length);
    }

    function updateAssignState() {
        // Der Termin ist nicht zwingend — es gibt Aufnahmen ohne Kalendereintrag.
        // Asana dagegen braucht Board und Abschnitt, sonst schlaegt die
        // Freigabe serverseitig mit 422 fehl.
        var asanaOk = !asanaCheckbox.checked ||
            (!!boardSelect.value && !!sectionSelect.value);
        assignBtn.disabled = !asanaOk;
    }

    function updateAsanaFieldState() {
        var off = !asanaCheckbox.checked;
        boardField.classList.toggle('hg-disabled', off);
        sectionField.classList.toggle('hg-disabled', off);
        updateAssignState();
    }

    function showAttendeeHint() {
        var ev = eventsById[eventSelect.value];
        if (!ev) {
            attendeeHint.textContent = '';
            return;
        }
        var names = ev.attendee_names || [];
        attendeeHint.textContent = names.length
            ? names.length + ' Eingeladene — dienen nur als Gegenprobe, ' +
              'die Teilnehmer kommen aus der Sprecherzuordnung'
            : 'Keine Eingeladenen im Termin hinterlegt';
    }

    // ------------------------------------------------------------------
    // Termine laden
    // ------------------------------------------------------------------
    function loadEvents() {
        var dateStr = (ctx.meetingDatetime || '').slice(0, 10);
        var url = '/api/calendar/events?date=' + encodeURIComponent(dateStr) +
            '&' + tokenParam;
        return fetch(url)
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (data) {
                var events = data.events || [];
                events.forEach(function (ev) { eventsById[ev.id] = ev; });

                fillSelect(eventSelect, events.map(function (ev) {
                    var time = ortszeit(ev.start);
                    return {
                        value: ev.id,
                        label: (time ? time + ' Uhr – ' : '') + ev.title,
                    };
                }), '– kein Termin –');

                // Vorauswahl: bester Titel-Match, sonst zeitlich naechster.
                // Der Plaud-Titel ist oft eine KI-Zusammenfassung des Inhalts,
                // deshalb greift der Titelvergleich nicht immer.
                var meetingTime = new Date(ctx.meetingDatetime).getTime();
                var best = null;
                var bestScore = 0;
                events.forEach(function (ev) {
                    var score = titleSimilarity(
                        ev.title, ctx.recordingTitle || ctx.meetingName
                    );
                    if (score > bestScore) { bestScore = score; best = ev; }
                });
                if (!best && events.length && !isNaN(meetingTime)) {
                    events.forEach(function (ev) {
                        var d = Math.abs(new Date(ev.start).getTime() - meetingTime);
                        if (!best || d < best._dist) { best = ev; best._dist = d; }
                    });
                }
                if (best) eventSelect.value = best.id;
                showAttendeeHint();
                updateAssignState();
            })
            .catch(function (err) {
                fillSelect(eventSelect, [], '⚠️ Termine konnten nicht geladen werden');
                console.error('Kalender-Fehler:', err);
            });
    }

    // ------------------------------------------------------------------
    // Asana-Boards + Abschnitte
    // ------------------------------------------------------------------
    function loadBoards(frisch) {
        // Die Board-Liste liegt 15 Minuten im Cache. Ein frisch in Asana
        // angelegtes Board waere sonst so lange unsichtbar, und der einzige
        // Ausweg war ein Neustart des Dienstes.
        var gewaehlt = boardSelect.value;
        return fetch('/api/asana/boards?' + tokenParam + (frisch ? '&refresh=true' : ''))
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (boards) {
                fillSelect(boardSelect, boards.map(function (b) {
                    return { value: b.gid, label: b.name };
                }), '– Board wählen –');
                if (frisch && gewaehlt) {
                    // Eine getroffene Wahl darf das Neuladen nicht verwerfen.
                    boardSelect.value = gewaehlt;
                } else {
                    wendeVorschlagAn();
                }
                updateAssignState();
            })
            .catch(function (err) {
                fillSelect(boardSelect, [], '⚠️ Boards konnten nicht geladen werden');
                console.error('Asana-Boards-Fehler:', err);
            });
    }

    function loadSections(boardGid, wunschSection, frisch) {
        sectionSelect.disabled = true;
        fillSelect(sectionSelect, [], 'Lade Abschnitte …');
        return fetch('/api/asana/boards/' + encodeURIComponent(boardGid) +
              '/sections?' + tokenParam + (frisch ? '&refresh=true' : ''))
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (sections) {
                fillSelect(sectionSelect, sections.map(function (s) {
                    return { value: s.gid, label: s.name };
                }), '– Abschnitt wählen –');
                sectionSelect.disabled = false;

                // Vorschlag aus der Historie schlaegt die Namensheuristik
                var gewuenscht = wunschSection && sections.some(function (s) {
                    return s.gid === wunschSection;
                });
                if (gewuenscht) {
                    sectionSelect.value = wunschSection;
                } else {
                    var proto = sections.find(function (s) {
                        return normalize(s.name) === 'protokolle';
                    });
                    if (proto) sectionSelect.value = proto.gid;
                }
                updateAssignState();
            })
            .catch(function (err) {
                fillSelect(sectionSelect, [], '⚠️ Abschnitte konnten nicht geladen werden');
                console.error('Asana-Sections-Fehler:', err);
            });
    }

    // ------------------------------------------------------------------
    // Freigabe
    // ------------------------------------------------------------------
    function assign() {
        var ev = eventsById[eventSelect.value];
        var payload = {
            event_id: eventSelect.value || null,
            eingeladene: (ev && ev.attendee_names) || [],
            create_asana_task: asanaCheckbox.checked,
            asana_board_gid: asanaCheckbox.checked ? (boardSelect.value || null) : null,
            asana_section_gid: asanaCheckbox.checked ? (sectionSelect.value || null) : null,
        };
        // Termindaten schlagen den Plaud-Titel und die Aufnahmezeit: der
        // Plaud-Titel ist eine KI-Zusammenfassung, die Aufnahmezeit kann vom
        // Termin abweichen, wenn spaeter mitgeschnitten wurde.
        if (ev) {
            payload.meeting_name = ev.title;
            payload.meeting_datetime = ev.start;
        }

        assignBtn.disabled = true;
        assignInfo.textContent = 'Wird freigegeben …';

        fetch('/api/protocols/' + encodeURIComponent(ctx.draftId) +
              '/assign?' + tokenParam, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(function (r) {
                if (r.status === 202) return r.json();
                return r.json()
                    .catch(function () { return {}; })
                    .then(function (body) {
                        throw new Error(body.detail || ('HTTP ' + r.status));
                    });
            })
            .then(function () {
                fertigMelden(
                    '✅ Freigegeben',
                    'Mara zieht jetzt das Transkript und erstellt den ' +
                    'Protokollentwurf. Du bekommst den Review-Link, sobald er fertig ist.'
                );
            })
            .catch(function (err) {
                assignInfo.textContent = '⚠️ ' + err.message;
                assignBtn.disabled = false;
                console.error('Freigabe-Fehler:', err);
            });
    }

    // ------------------------------------------------------------------
    // Sprecher aus Plaud — Kontrolle vor der Freigabe
    // ------------------------------------------------------------------
    function ladeSprecher() {
        speakerList.textContent = 'Wird geladen …';
        fetch('/api/protocols/' + encodeURIComponent(ctx.draftId) +
              '/plaud-speakers?' + tokenParam)
            .then(function (r) {
                if (!r.ok) {
                    return r.json().catch(function () { return {}; })
                        .then(function (b) { throw new Error(b.detail || ('HTTP ' + r.status)); });
                }
                return r.json();
            })
            .then(function (d) {
                var liste = d.sprecher || [];
                if (!liste.length) {
                    speakerList.textContent = 'Noch kein Transkript in Plaud.';
                    return;
                }
                var html = '<ul class="hg-speaker-ul">';
                liste.forEach(function (s) {
                    var anteil = d.segmente ? Math.round(100 * s.segmente / d.segmente) : 0;
                    var unbenannt = /^speaker/i.test(s.name) || s.name === '(ohne Namen)';
                    html += '<li' + (unbenannt ? ' class="hg-speaker-offen"' : '') + '>' +
                        escapeHtml(s.name) + ' <span class="hg-speaker-anteil">' +
                        s.segmente + ' Beiträge, ' + anteil + ' %</span></li>';
                });
                html += '</ul>';
                if (d.unbenannt) {
                    html += '<p class="hg-speaker-hinweis">' + d.unbenannt +
                        ' von ' + d.segmente + ' Beiträgen ohne Namen. ' +
                        'In Plaud ergänzen, dann hier neu laden.</p>';
                }
                speakerList.innerHTML = html;
            })
            .catch(function (err) {
                speakerList.textContent = '⚠️ ' + err.message;
            });
    }

    function escapeHtml(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    // ------------------------------------------------------------------
    // Asana-Board aus der Historie vorschlagen
    // ------------------------------------------------------------------
    var vorschlag = null;

    function ladeBoardVorschlag() {
        return fetch('/api/protocols/' + encodeURIComponent(ctx.draftId) +
                     '/board-suggestion?' + tokenParam)
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (d && d.gefunden) vorschlag = d;
            })
            .catch(function () { /* Vorschlag ist Komfort, kein Muss */ });
    }

    function wendeVorschlagAn() {
        // Nur setzen, wenn der Nutzer noch nichts gewaehlt hat.
        if (!vorschlag || boardSelect.value) return;
        var da = Array.prototype.some.call(boardSelect.options, function (o) {
            return o.value === vorschlag.asana_board_gid;
        });
        if (!da) return;
        boardSelect.value = vorschlag.asana_board_gid;
        loadSections(vorschlag.asana_board_gid, vorschlag.asana_section_gid);
        if (vorschlag.quelle && vorschlag.quelle.meeting_datetime) {
            assignInfo.textContent = 'Board übernommen vom ' +
                vorschlag.quelle.meeting_datetime.slice(0, 10);
        }
    }

    // ------------------------------------------------------------------
    // Verwerfen
    // ------------------------------------------------------------------
    function discard() {
        // Bewusst ein natives confirm() statt eines Modals: eine destruktive
        // Ja/Nein-Frage braucht keinen eigenen DOM-Zustand, und der Editor
        // hatte genau damit schon Ärger (hidden vs. display:flex).
        var name = ctx.recordingTitle || ctx.meetingName || 'diese Aufnahme';
        if (!window.confirm(
            'Aufnahme verwerfen?\n\n' + name + '\n\n' +
            'Es wird kein Protokoll erstellt und du wirst nicht mehr erinnert. ' +
            'Die Aufnahme selbst bleibt in Plaud erhalten.'
        )) {
            return;
        }

        discardBtn.disabled = true;
        assignBtn.disabled = true;
        assignInfo.textContent = 'Wird verworfen …';

        fetch('/api/protocols/' + encodeURIComponent(ctx.draftId) +
              '/discard?' + tokenParam, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'Fehlaufnahme' }),
        })
            .then(function (r) {
                if (r.ok) return r.json();
                return r.json()
                    .catch(function () { return {}; })
                    .then(function (body) {
                        throw new Error(body.detail || ('HTTP ' + r.status));
                    });
            })
            .then(function () {
                fertigMelden(
                    '🗑 Verworfen',
                    'Zu dieser Aufnahme wird kein Protokoll erstellt. In Plaud ' +
                    'bleibt sie erhalten — dort kannst du sie bei Bedarf löschen.'
                );
            })
            .catch(function (err) {
                assignInfo.textContent = '⚠️ ' + err.message;
                discardBtn.disabled = false;
                updateAssignState();
                console.error('Verwerfen-Fehler:', err);
            });
    }

    /**
     * Gemeinsames Verhalten der „neu laden"-Knöpfe: während des Ladens
     * gesperrt und beschriftet, danach zurück. Ohne diese Rückmeldung
     * klickt man zweimal, weil nichts zu passieren scheint.
     */
    function mitLadeanzeige(knopf, aufgabe) {
        if (!knopf || knopf.disabled) { return; }
        var text = knopf.textContent;
        knopf.disabled = true;
        knopf.textContent = 'lädt …';
        var fertig = function () {
            knopf.disabled = false;
            knopf.textContent = text;
        };
        var ergebnis = aufgabe();
        if (ergebnis && typeof ergebnis.then === 'function') {
            ergebnis.then(fertig, fertig);
        } else {
            window.setTimeout(fertig, 600);
        }
    }

    // ------------------------------------------------------------------
    // Verdrahtung
    // ------------------------------------------------------------------
    boardSelect.addEventListener('change', function () {
        if (boardSelect.value) {
            loadSections(boardSelect.value);
        } else {
            fillSelect(sectionSelect, [], 'Erst Board wählen …');
            sectionSelect.disabled = true;
        }
        updateAssignState();
    });
    eventSelect.addEventListener('change', function () {
        showAttendeeHint();
        updateAssignState();
    });
    sectionSelect.addEventListener('change', updateAssignState);
    asanaCheckbox.addEventListener('change', updateAsanaFieldState);
    assignBtn.addEventListener('click', assign);
    discardBtn.addEventListener('click', discard);
    speakerReload.addEventListener('click', ladeSprecher);

    var eventReload = document.getElementById('event-reload');
    var boardReload = document.getElementById('board-reload');
    if (eventReload) {
        eventReload.addEventListener('click', function () {
            // Outlook wird nicht zwischengespeichert — ein erneuter Abruf
            // genuegt, etwa nach einem nachtraeglich angelegten Termin.
            mitLadeanzeige(eventReload, loadEvents);
        });
    }
    if (boardReload) {
        boardReload.addEventListener('click', function () {
            mitLadeanzeige(boardReload, function () {
                return loadBoards(true).then(function () {
                    // Die Abschnitte des gewaehlten Boards ebenfalls frisch —
                    // ein neues Board hat oft auch neue Abschnitte.
                    if (boardSelect.value) {
                        return loadSections(boardSelect.value, sectionSelect.value, true);
                    }
                });
            });
        });
    }

    updateAsanaFieldState();
    loadEvents();
    ladeSprecher();
    // Vorschlag vor den Boards holen, damit er beim Befuellen schon vorliegt
    ladeBoardVorschlag().then(loadBoards);
})();
