/*
 * Herbert Gruppe – Protokoll-Review-Editor
 *
 * Aufgaben:
 *  1. Outlook-Termine laden + Vorauswahl (Titel-Ähnlichkeit / zeitliche Nähe)
 *  2. Asana-Boards laden
 *  3. Sections nach Board-Wahl laden + Vorauswahl "Protokolle"
 *  4. Freigeben-Button-Gating (Termin + ggf. Board/Section je nach Checkbox)
 *  5. Speichern: PATCH, Auto-Save alle 60 Sekunden + manuell
 *  6. Freigeben: POST approve → Redirect auf Erfolgsseite
 *  7. Ablehnen: Modal → POST reject → Reload
 */
(function () {
    'use strict';

    var ctx = window.REVIEW_CONTEXT;
    var apiBase = '/api/protocols/' + encodeURIComponent(ctx.draftId);
    var tokenParam = 'token=' + encodeURIComponent(ctx.token);

    var eventSelect = document.getElementById('event-select');
    var boardSelect = document.getElementById('board-select');
    var sectionSelect = document.getElementById('section-select');
    var asanaCheckbox = document.getElementById('asana-checkbox');
    var boardField = document.getElementById('board-field');
    var sectionField = document.getElementById('section-field');
    var approveBtn = document.getElementById('approve-btn');
    var saveBtn = document.getElementById('save-btn');
    var rejectBtn = document.getElementById('reject-btn');
    var saveInfo = document.getElementById('save-info');
    var previewPane = document.getElementById('preview-pane');
    var editorGrid = document.getElementById('editor-grid');

    var dirty = false;

    // ------------------------------------------------------------------
    // Markdown-Editor (SimpleMDE) + Live-Preview
    // ------------------------------------------------------------------
    var simplemde = new SimpleMDE({
        element: document.getElementById('markdown-editor'),
        spellChecker: false,
        status: false,
        autofocus: false,
        toolbar: ['bold', 'italic', 'heading', '|', 'unordered-list',
                  'ordered-list', 'table', '|', 'undo', 'redo'],
    });

    var previewTimer = null;
    function renderPreview() {
        previewPane.innerHTML = simplemde.markdown(simplemde.value());
    }
    simplemde.codemirror.on('change', function () {
        dirty = true;
        saveInfo.textContent = 'Ungespeicherte Änderungen';
        clearTimeout(previewTimer);
        previewTimer = setTimeout(renderPreview, 300);
    });
    renderPreview();

    // Mobile-Tabs Editor/Vorschau
    document.querySelectorAll('.hg-tab').forEach(function (tab) {
        tab.addEventListener('click', function () {
            document.querySelectorAll('.hg-tab').forEach(function (t) {
                t.classList.remove('active');
            });
            tab.classList.add('active');
            if (tab.dataset.tab === 'preview') {
                renderPreview();
                editorGrid.classList.add('hg-show-preview');
            } else {
                editorGrid.classList.remove('hg-show-preview');
                simplemde.codemirror.refresh();
            }
        });
    });

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------
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
     *
     * Dieselbe Funktion steht in review_assign.js — die beiden Dateien
     * teilen bewusst keinen Code, siehe Kopfkommentar dort.
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

    // Einfache Titel-Ähnlichkeit: Anteil gemeinsamer Wörter
    function titleSimilarity(a, b) {
        var wa = normalize(a).split(' ').filter(Boolean);
        var wb = normalize(b).split(' ').filter(Boolean);
        if (!wa.length || !wb.length) return 0;
        var common = wa.filter(function (w) { return wb.indexOf(w) !== -1; });
        return common.length / Math.max(wa.length, wb.length);
    }

    function updateApproveState() {
        var hasEvent = !!eventSelect.value;
        var asanaOk = !asanaCheckbox.checked ||
            (!!boardSelect.value && !!sectionSelect.value);
        approveBtn.disabled = !(hasEvent && asanaOk);
    }

    function updateAsanaFieldState() {
        var off = !asanaCheckbox.checked;
        boardField.classList.toggle('hg-disabled', off);
        sectionField.classList.toggle('hg-disabled', off);
        updateApproveState();
    }

    // ------------------------------------------------------------------
    // 1. Outlook-Termine laden
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
                fillSelect(eventSelect, events.map(function (ev) {
                    var time = ortszeit(ev.start);
                    return {
                        value: ev.id,
                        label: (time ? time + ' Uhr – ' : '') + ev.title,
                    };
                }), '– Termin wählen –');

                // Was in der Zuordnungs-Stufe gewählt wurde, gilt. Es ist eine
                // getroffene Entscheidung und darf nicht von einem Ratevorgang
                // überschrieben werden.
                if (ctx.eventId) {
                    eventSelect.value = ctx.eventId;
                    if (eventSelect.value === ctx.eventId) {
                        updateApproveState();
                        return;
                    }
                    // Termin steht nicht mehr in der Liste (verschoben oder
                    // abgesagt) — dann unten weiterraten statt leer lassen.
                }

                // Vorauswahl: bester Titel-Match, sonst zeitlich nächster
                var meetingTime = new Date(ctx.meetingDatetime).getTime();
                var best = null;
                var bestScore = 0;
                events.forEach(function (ev) {
                    var score = titleSimilarity(ev.title, ctx.meetingName);
                    if (score > bestScore) { bestScore = score; best = ev; }
                });
                if (!best && events.length && !isNaN(meetingTime)) {
                    events.forEach(function (ev) {
                        var d = Math.abs(new Date(ev.start).getTime() - meetingTime);
                        if (!best || d < best._dist) { best = ev; best._dist = d; }
                    });
                }
                if (best) eventSelect.value = best.id;
                updateApproveState();
            })
            .catch(function (err) {
                fillSelect(eventSelect, [], '⚠️ Termine konnten nicht geladen werden');
                console.error('Kalender-Fehler:', err);
            });
    }

    // ------------------------------------------------------------------
    // 2./3. Asana-Boards + Sections laden
    // ------------------------------------------------------------------
    function loadBoards(frisch) {
        // 15-Minuten-Cache: ein frisch in Asana angelegtes Board waere sonst
        // so lange unsichtbar.
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
                } else if (ctx.asanaBoardGid) {
                    // Aus der Zuordnungs-Stufe übernehmen, samt Abschnitt.
                    boardSelect.value = ctx.asanaBoardGid;
                    if (boardSelect.value === ctx.asanaBoardGid) {
                        loadSections(ctx.asanaBoardGid);
                    }
                }
                updateApproveState();
            })
            .catch(function (err) {
                fillSelect(boardSelect, [], '⚠️ Boards konnten nicht geladen werden');
                console.error('Asana-Boards-Fehler:', err);
            });
    }

    function loadSections(boardGid, frisch) {
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

                // Was in der Zuordnungs-Stufe gewählt wurde, gilt.
                if (ctx.asanaSectionGid) {
                    sectionSelect.value = ctx.asanaSectionGid;
                    if (sectionSelect.value === ctx.asanaSectionGid) {
                        updateApproveState();
                        return;
                    }
                }

                // Vorauswahl: Abschnitt "Protokolle" falls vorhanden
                var proto = sections.find(function (s) {
                    return normalize(s.name) === 'protokolle';
                });
                if (proto) sectionSelect.value = proto.gid;
                updateApproveState();
            })
            .catch(function (err) {
                fillSelect(sectionSelect, [], '⚠️ Abschnitte konnten nicht geladen werden');
                console.error('Asana-Sections-Fehler:', err);
            });
    }

    boardSelect.addEventListener('change', function () {
        if (boardSelect.value) {
            loadSections(boardSelect.value);
        } else {
            fillSelect(sectionSelect, [], 'Erst Board wählen …');
            sectionSelect.disabled = true;
        }
        updateApproveState();
    });
    eventSelect.addEventListener('change', updateApproveState);
    sectionSelect.addEventListener('change', updateApproveState);
    asanaCheckbox.addEventListener('change', updateAsanaFieldState);

    // ------------------------------------------------------------------
    // 5. Speichern (manuell + Auto-Save 60 s)
    // ------------------------------------------------------------------
    function save(manual) {
        if (!dirty && !manual) return Promise.resolve();
        return fetch(apiBase + '?' + tokenParam, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ markdown: simplemde.value() }),
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            dirty = false;
            var now = new Date();
            saveInfo.textContent = 'Gespeichert um ' +
                now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
        }).catch(function (err) {
            saveInfo.textContent = '⚠️ Speichern fehlgeschlagen';
            console.error('Speichern-Fehler:', err);
            throw err;
        });
    }

    saveBtn.addEventListener('click', function () { save(true); });
    setInterval(function () { save(false); }, 60000);

    window.addEventListener('beforeunload', function (e) {
        if (dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    // ------------------------------------------------------------------
    // 6. Freigeben
    // ------------------------------------------------------------------
    approveBtn.addEventListener('click', function () {
        approveBtn.disabled = true;
        approveBtn.textContent = '⏳ Wird freigegeben …';

        save(true)
            .then(function () {
                return fetch(apiBase + '/approve?' + tokenParam, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        event_id: eventSelect.value,
                        create_asana_task: asanaCheckbox.checked,
                        asana_board_gid: asanaCheckbox.checked ? boardSelect.value : null,
                        asana_section_gid: asanaCheckbox.checked ? sectionSelect.value : null,
                    }),
                });
            })
            .then(function (r) {
                if (r.status !== 202) {
                    return r.json().then(function (body) {
                        throw new Error(body.detail || ('HTTP ' + r.status));
                    });
                }
                dirty = false;
                // Wer die Freigaben am Stueck abarbeitet, will zurueck zur
                // Liste statt auf eine Sackgassen-Seite. Die Erfolgsseite
                // bleibt fuer alle anderen Wege erhalten.
                if (new URLSearchParams(window.location.search).get('weiter') === 'uebersicht') {
                    window.location.href = '/protokolle';
                } else {
                    window.location.href = '/review/' +
                        encodeURIComponent(ctx.token) + '/success';
                }
            })
            .catch(function (err) {
                alert('Freigabe fehlgeschlagen: ' + err.message);
                approveBtn.textContent = '✅ Freigeben';
                updateApproveState();
            });
    });

    // ------------------------------------------------------------------
    // 7. Ablehnen (Modal)
    // ------------------------------------------------------------------
    var rejectModal = document.getElementById('reject-modal');
    var rejectReason = document.getElementById('reject-reason');

    rejectBtn.addEventListener('click', function () {
        rejectModal.hidden = false;
        rejectReason.focus();
    });
    document.getElementById('reject-cancel').addEventListener('click', function () {
        rejectModal.hidden = true;
    });
    document.getElementById('reject-confirm').addEventListener('click', function () {
        var reason = rejectReason.value.trim();
        if (!reason) {
            rejectReason.focus();
            return;
        }
        fetch(apiBase + '/reject?' + tokenParam, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: reason }),
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            dirty = false;
            window.location.reload();
        }).catch(function (err) {
            alert('Ablehnen fehlgeschlagen: ' + err.message);
        });
    });

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
                    // Ein neues Board hat oft auch neue Abschnitte.
                    if (boardSelect.value) {
                        return loadSections(boardSelect.value, true);
                    }
                });
            });
        });
    }

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------
    updateAsanaFieldState();
    loadEvents();
    loadBoards();
})();
