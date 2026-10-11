/* Existing pages use the same authenticated JSON API as other consumers. */
(async function () {
  "use strict";
  const pageData = document.getElementById("setmaker-page-data");
  const main = document.querySelector("main");
  if (!pageData || !main || main.dataset.integrationReady) return;
  main.dataset.integrationReady = "true";
  const data = JSON.parse(pageData.textContent);
  const notice = document.createElement("p");
  notice.id = "integration-notice";
  notice.setAttribute("role", "status");
  main.prepend(notice);
  let csrf;
  async function api(path, method = "GET", body) {
    const headers = {Accept: "application/json"};
    if (method !== "GET") {
      if (!csrf) {
        const response = await fetch("/csrf-token", {credentials: "same-origin"});
        if (!response.ok) throw new Error("Could not obtain CSRF token.");
        csrf = (await response.json()).csrf_token;
      }
      headers["X-CSRFToken"] = csrf;
      if (body !== undefined) headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body)});
    const result = response.status === 204 ? {} : await response.json();
    if (!response.ok) {
      const error = new Error(result.message || "Request failed.");
      error.code = result.error;
      throw error;
    }
    return result;
  }
  const showError = error => { notice.textContent = error.message; };
  function roundFormat(value) {
    if (["Mixed Doubles", "mixedDoubles"].includes(value)) return "mixed";
    if (["Same-Gender Doubles", "sameGenderDoubles"].includes(value)) return "same_gender";
    if (["Open Play (Any Gender)", "openPlay"].includes(value)) return "open";
    throw new Error("That round format is not available yet.");
  }
  function element(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.append(node);
    return node;
  }
  function genderDotClass(gender) {
    const value = (gender || "").toUpperCase();

    if (value === "F" || value === "FEMALE") {
        return "female";
    }

    if (value === "M" || value === "MALE") {
        return "male";
    }

    return "unknown";
}
  function button(text, parent, action) {
    const node = element("button", text, parent);
    node.type = "button";
    node.className = "btn";
    node.onclick = async () => {
      node.disabled = true;
      try { await action(); } catch (error) { showError(error); }
      finally { node.disabled = false; }
    };
    return node;
  }
  function field(form, name, label, value, type = "text") {
    let input = form.elements?.namedItem(name) || form.querySelector(`[name="${name}"]`);
    if (!input) {
      const row = element("div", undefined, form);
      row.className = "field";
      const caption = element("label", label, row);
      input = element("input", undefined, row);
      input.id = name;
      caption.htmlFor = name;
      input.name = name;
      input.type = type;
      input.className = "input";
    }
    input.value = value ?? "";
    return input;
  }
  function choices(container, items, name, selected = []) {
    container.replaceChildren();
    for (const item of items) {
      const label = element("label", undefined, container);
      label.className = "choice";
      const input = element("input", undefined, label);
      input.type = "checkbox";
      input.name = name;
      input.value = item.id;
      input.checked = selected.includes(item.id);
      label.append(document.createTextNode(item.name || `Court ${item.number} (${item.court_type})`));
    }
  }
  function attachForm(form, save) {
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      notice.textContent = "";
      try { await save(new FormData(form)); }
      catch (error) { showError(error); }
      finally { submit.disabled = false; }
    };
  }
  try {
    if (data.page === "players" && !data.can_manage) {
      document.querySelectorAll(".delete-form").forEach(form => form.remove());
    }
    if (data.page === "player-form") {
      const form = document.querySelector("form");
      if (!data.can_manage_skill) {
        form.elements.rating.disabled = true;
        form.elements.rating.required = false;
      }
      const confirm = element("label", undefined, form);
      const check = element("input", undefined, confirm);
      check.type = "checkbox";
      check.name = "confirm_duplicate";
      confirm.append(document.createTextNode("Confirm this is a different person if a duplicate warning appears."));
      attachForm(form, async values => {
        const payload = {name: values.get("name"), gender: values.get("gender") === "Male" ? "M" : "F",
          notes: values.get("notes"), court_preference: values.get("court_preference"),
          confirm_duplicate: values.has("confirm_duplicate")};
        if (data.can_manage_skill) payload.skill = Number(values.get("rating"));
        const id = data.player?.id;
        await api(id ? `/api/players/${id}` : "/api/players", id ? "PATCH" : "POST", payload);
        location.assign("/players");
      });
    }
    if (data.page === "courts") {
      document.querySelectorAll("[data-court-toggle]").forEach(toggle => {
        toggle.onchange = async () => {
          toggle.disabled = true;
          try {
            await api(`/api/courts/${toggle.dataset.courtToggle}`, "PATCH", {active: toggle.checked});
            location.reload();
          } catch (error) { toggle.checked = !toggle.checked; showError(error); }
          finally { toggle.disabled = false; }
        };
      });
    }
    if (data.page === "court-form") {
      const form = document.querySelector("form");
      if (data.court) {
        form.elements["court-name"].value = data.court.number;
        const surface = data.court.court_type.charAt(0).toUpperCase() + data.court.court_type.slice(1).toLowerCase();
        if (!Array.from(form.elements.surface.options).some(option => option.value === surface)) {
          const option = element("option", data.court.court_type, form.elements.surface);
          option.value = surface;
        }
        form.elements.surface.value = surface;
        form.elements.cover.value = data.court.cover || "";
        form.elements.priority.value = data.court.priority;
        form.querySelector('[type="checkbox"]').checked = data.court.active;
        form.querySelector('[type="submit"]').textContent = "Save Court";
      }
      form.elements.priority.min = "0";
      attachForm(form, async values => {
        const payload = {number: Number(values.get("court-name")), court_type: values.get("surface"),
          cover: values.get("cover"), priority: Number(values.get("priority") || 0),
          active: form.querySelector('[type="checkbox"]').checked};
        await api(data.court ? `/api/courts/${data.court.id}` : "/api/courts", data.court ? "PATCH" : "POST", payload);
        location.assign("/courts");
      });
    }
    if (data.page === "template-form" || data.page === "session-form") {
      const form = document.querySelector("form");
      const courts = (await api("/api/courts")).courts.filter(court => court.active);
      const template = data.template;
      if (data.page === "template-form") {
        form.elements["template-name"].value = template?.name || "";
        form.elements.format.value = {mixed: "Mixed Doubles", same_gender: "Same-Gender Doubles", open: "Open Play (Any Gender)"}[template?.round_format || "open"];
        field(form, "rounds", "Number of Rounds", template?.number_of_rounds || 6, "number");
        field(form, "round-length", "Round Length (minutes)", template?.round_duration_minutes || 30, "number");
        field(form, "start_time", "Start Time", template?.start_time || "13:00", "time");
        field(form, "session_duration_minutes", "Session Duration (minutes)", template?.session_duration_minutes || 180, "number");
        field(form, "tea_break_after_round", "Tea Break After Round (optional)", template?.tea_break_after_round, "number");
        const panel = document.querySelector(".template-priority-list .court-list");
        choices(panel, courts, "court_ids", template?.court_ids || courts.map(court => court.id));
        attachForm(form, async values => {
          const useDefaults = document.getElementById("use-default-priority").checked;
          const payload = {name: values.get("template-name"), number_of_rounds: Number(values.get("rounds")),
            round_duration_minutes: Number(values.get("round-length")), start_time: values.get("start_time"),
            session_duration_minutes: Number(values.get("session_duration_minutes")),
            round_format: roundFormat(values.get("format")),
            tea_break_after_round: values.get("tea_break_after_round") ? Number(values.get("tea_break_after_round")) : null,
            court_ids: useDefaults ? courts.map(court => court.id) : values.getAll("court_ids")};
          await api(template ? `/api/session-templates/${template.id}` : "/api/session-templates", template ? "PATCH" : "POST", payload);
          location.assign("/templates");
        });
      } else {
        const players = (await api("/api/players")).players.filter(player => player.skill_ready);
        form.elements.sessionName.value = template?.name || "";
        form.elements.startTime.value = template?.start_time || "13:00";
        const roundsInput = document.getElementById("roundCount");

        roundsInput.value = template?.number_of_rounds || 6;
        const duration = form.elements.roundDuration;
        for (const minutes of [20, 25, 30, 45, 60, template?.round_duration_minutes].filter(Boolean)) {
          const option = element("option", `${minutes} minutes`, duration); option.value = minutes;
        }
        duration.value = template?.round_duration_minutes || 30;
        field(form, "session_duration_minutes", "Session Duration (minutes)", template?.session_duration_minutes || 180, "number");
        field(form, "tea_break_after_round", "Tea Break After Round (optional)", template?.tea_break_after_round, "number");
        form.elements.matchFormat.value = {mixed: "mixedDoubles", same_gender: "sameGenderDoubles", open: "openPlay"}[template?.round_format || "open"];
        form.elements.allowRelaxation.checked = !template?.strict_format;
        choices(document.getElementById("courtOptions"), courts, "court_ids", template?.court_ids || courts.map(court => court.id));
        const playerChoices = element("section", undefined, form);
        element("h2", "Attending Players", playerChoices);
        const list = element("div", undefined, playerChoices);
        choices(list, players, "player_ids");
        form.elements.venue.closest(".field").hidden = true;
        document.querySelector(".segmented").closest(".field").hidden = true;
        attachForm(form, async values => {
          const payload = {name: values.get("sessionName"), starts_at: `${values.get("sessionDate")}T${values.get("startTime")}`,
            number_of_rounds: Number(values.get("number_of_rounds")), round_duration_minutes: Number(values.get("roundDuration")),
            session_duration_minutes: Number(values.get("session_duration_minutes")),
            round_format: roundFormat(values.get("matchFormat")),
            strict_format: !values.has("allowRelaxation"), court_ids: values.getAll("court_ids"), player_ids: values.getAll("player_ids"),
            tea_break_after_round: values.get("tea_break_after_round") ? Number(values.get("tea_break_after_round")) : null};
          if (template) payload.template_id = template.id;
          const session = (await api("/api/sessions", "POST", payload)).session;
          // Persist configuration first; generation is retryable on the live page.
          try { await api(`/api/sessions/${session.id}/rounds/generate`, "POST"); }
          catch (error) { location.assign(`/sessions/${session.id}?generation_failed=1`); return; }
          location.assign(`/sessions/${session.id}`);
        });
      }
    }
    if (data.page === "sessions") {
      const sessions = (await api("/api/sessions")).sessions;
      const list = document.getElementById("sessionsPageList");
      const search = document.getElementById("sessionSearch");
      const date = document.getElementById("sessionDateFilter");
      const mode = document.getElementById("sessionModeFilter");
      const formats = {open: "Open Play", mixed: "Mixed Doubles", same_gender: "Same-Gender Doubles"};
      let statusFilter = "";
      mode.replaceChildren();
      for (const [value, label] of [["", "All Formats"], ...Object.entries(formats)]) {
        element("option", label, mode).value = value;
      }
      for (const [id, status] of [["activeSessionCount", "active"], ["upcomingSessionCount", "draft"], ["completedSessionCount", "completed"]]) {
        document.getElementById(id).textContent = sessions.filter(s => s.status === status).length;
        const tab = document.getElementById(id).closest("button");
        tab.classList.remove("active");
        tab.onclick = () => {
          statusFilter = status;
          document.querySelectorAll(".session-tab").forEach(other => other.classList.toggle("active", other === tab));
          drawSessions();
        };
      }
      function drawSessions() {
        const visible = sessions.filter(s => (!statusFilter || s.status === statusFilter) &&
          s.name.toLowerCase().includes(search.value.trim().toLowerCase()) &&
          (!date.value || s.starts_at.slice(0, 10) === date.value) &&
          (!mode.value || s.round_format === mode.value));
        list.replaceChildren();
        for (const session of visible) {
          const row = element("article", undefined, list);
          const link = element("a", session.name, row);
          link.href = `/sessions/${session.id}`;
          row.className = "card";
          element("p", `${session.starts_at} · ${session.status} · ${formats[session.round_format]}`, row);
        }
        document.getElementById("sessionsEmptyState").hidden = visible.length > 0;
        document.getElementById("sessionsResultCount").textContent = `${visible.length} sessions`;
      }
      for (const input of [search, date, mode]) input.addEventListener("input", drawSessions);
      document.getElementById("clearSessionFilters").onclick = () => {
        for (const input of [search, date, mode]) input.value = "";
        statusFilter = "";
        document.querySelectorAll(".session-tab").forEach(tab => tab.classList.remove("active"));
        drawSessions();
      };
      document.querySelector(".sessions-pagination")?.remove();
      drawSessions();
    }
    if (data.page === "accounts") {
      const [members, invitations, login] = await Promise.all([api("/api/users"), api("/api/invitations"), api("/check-session")]);
      const ownRole = login.user.role;
      const search = document.getElementById("accountSearch");
      const roleFilter = document.getElementById("roleFilter");
      const statusFilter = document.getElementById("statusFilter");
      const list = document.getElementById("accountTableBody");
      const add = document.getElementById("addAccountButton");
      add.textContent = "Invite Member";
      add.onclick = () => location.assign("/settings/invite-member");
      const rows = members.users.map(u => ({...u, status: u.active ? "active" : "disabled"}));
      rows.push(...invitations.invitations.filter(i => !i.accepted_at && new Date(`${i.expires_at}Z`) > new Date()).map(i => ({...i, status: "pending", invitation: true})));
      function drawAccounts() {
        const visible = rows.filter(u => `${u.full_name || ""} ${u.email}`.toLowerCase().includes(search.value.trim().toLowerCase()) &&
          (!roleFilter.value || u.role === roleFilter.value) && (!statusFilter.value || u.status === statusFilter.value));
        list.replaceChildren();
        for (const member of visible) {
          const row = element("tr", undefined, list);
          for (const value of [member.full_name || "—", member.email, member.role, member.status, "—"]) element("td", value, row);
          const actions = element("td", undefined, row);
          if (member.role === "owner" || (ownRole !== "owner" && member.role !== "staff") || member.status === "disabled") continue;
          if (!member.invitation) {
            const role = element("select", undefined, actions);
            for (const value of ownRole === "owner" ? ["admin", "staff"] : ["staff"]) element("option", value, role).value = value;
            role.value = member.role;
            button("Save Role", actions, async () => { await api(`/api/users/${member.id}`, "PATCH", {role: role.value}); location.reload(); });
          }
          button(member.invitation ? "Revoke" : "Remove", actions, async () => {
            if (!window.confirm(`${member.invitation ? "Revoke invitation for" : "Remove"} ${member.email}?`)) return;
            await api(`/api/${member.invitation ? "invitations" : "users"}/${member.id}`, "DELETE"); location.reload();
          });
        }
        document.getElementById("accountsEmptyState").hidden = visible.length > 0;
        document.getElementById("accountCount").textContent = `${visible.length} accounts and pending invitations`;
      }
      for (const input of [search, roleFilter, statusFilter]) input.addEventListener("input", drawAccounts);
      document.getElementById("clearAccountFilters").onclick = () => { for (const input of [search, roleFilter, statusFilter]) input.value = ""; drawAccounts(); };
      document.querySelector(".account-pagination")?.remove();
      drawAccounts();
    }
    if (data.page === "dashboard") {
      const [sessions, players, courts] = await Promise.all([api("/api/sessions"), api("/api/players"), api("/api/courts")]);
      document.getElementById("registeredPlayers").textContent = players.players.length;
      document.getElementById("availableCourts").textContent = courts.courts.filter(court => court.active).length;
      const active = sessions.sessions.filter(session => ["draft", "active"].includes(session.status));
      document.getElementById("activeSessions").textContent = active.length;
      document.getElementById("nextRound").textContent = "Open a session";
      const list = document.getElementById("sessionList");
      for (const session of active) {
        const link = element("a", `${session.name} · ${session.starts_at} · ${session.status}`, list);
        link.href = `/sessions/${session.id}`; link.className = "card";
      }
      if (!active.length) element("p", "No active sessions yet.", list);
      document.getElementById("recentActivity").textContent = "Open a session to view its saved rounds.";
    }
    if (data.page === "history") {
        const response = await api("/api/sessions");
        const formats = {open: "Open Play", mixed: "Mixed Doubles", same_gender: "Same-Gender Doubles"};

        const completedSessions = response.sessions.filter(
            session => session.status === "completed"
        );

        const tableBody = document.getElementById("historyTableBody");
        const emptyState = document.getElementById("historyEmptyState");
        const count = document.getElementById("historyCount");

        const search = document.getElementById("historySearch");
        const date = document.getElementById("historyDate");
        const type = document.getElementById("historyType");
        const clearButton = document.getElementById("clearHistoryFilters");
        type.replaceChildren();
        for (const [value, label] of [["", "All Formats"], ...Object.entries(formats)]) {
            element("option", label, type).value = value;
        }

        const pagination = document.querySelector(".history-pagination");
        const pageButtons = pagination.querySelectorAll("button");

        const previousButton = pageButtons[0];
        const pageNumberButton = pageButtons[1];
        const nextButton = pageButtons[2];

        const pageSize = 10;
        let currentPage = 1;

        function renderHistory() {
            const query = search.value.trim().toLowerCase();
            const selectedDate = date.value;
            const selectedType = type.value;

            const filtered = completedSessions.filter(session => {
                const matchesName = session.name.toLowerCase().includes(query);

                const matchesDate =
                    !selectedDate ||
                    session.starts_at.slice(0, 10) === selectedDate;

                const matchesType =
                    !selectedType ||
                    session.round_format === selectedType;

                return matchesName && matchesDate && matchesType;
            });

            const totalPages = Math.max(
                1,
                Math.ceil(filtered.length / pageSize)
            );

            currentPage = Math.min(currentPage, totalPages);

            const startIndex = (currentPage - 1) * pageSize;
            const visibleSessions = filtered.slice(
                startIndex,
                startIndex + pageSize
            );

            tableBody.replaceChildren();

            for (const session of visibleSessions) {
                const row = element("tr", undefined, tableBody);

                const sessionCell = element("td", undefined, row);
                const sessionLink = element(
                    "a",
                    session.name,
                    sessionCell
                );
                sessionLink.href = `/sessions/${session.id}`;
                sessionLink.style.fontWeight = "700";
                sessionLink.style.color = "var(--green)";

                const dateCell = element("td", undefined, row);
                dateCell.textContent = session.starts_at
                    .slice(0, 10);

                element(
                    "td",
                    String(session.participants.length),
                    row
                );

                element(
                    "td",
                    String(session.court_ids.length),
                    row
                );

                element(
                    "td",
                    `${session.number_of_rounds} planned`,
                    row
                );

                element(
                    "td",
                    formats[session.round_format] || session.round_format,
                    row
                );

                const actionCell = element("td", undefined, row);

                const viewLink = element(
                    "a",
                    "View Results",
                    actionCell
                );

                viewLink.href = `/sessions/${session.id}`;
                viewLink.className = "btn btn-sm";
            }

            emptyState.hidden = filtered.length > 0;
            emptyState.style.display =
                filtered.length > 0 ? "none" : "";

            count.textContent =
                `${filtered.length} completed session${filtered.length === 1 ? "" : "s"}`;

            pageNumberButton.textContent =
                `${currentPage} / ${totalPages}`;

            previousButton.disabled = currentPage <= 1;
            nextButton.disabled = currentPage >= totalPages;
            pageNumberButton.disabled = true;
        }

        function resetAndRender() {
            currentPage = 1;
            renderHistory();
        }

        search.addEventListener("input", resetAndRender);
        date.addEventListener("change", resetAndRender);
        type.addEventListener("change", resetAndRender);

        clearButton.addEventListener("click", () => {
            search.value = "";
            date.value = "";
            type.value = "";

            resetAndRender();
        });

        previousButton.addEventListener("click", () => {
            if (currentPage > 1) {
                currentPage--;
                renderHistory();
            }
        });

        nextButton.addEventListener("click", () => {
            currentPage++;
            renderHistory();
        });

        renderHistory();
    }
    if (data.page === "active-session") {
      const session = (await api(`/api/sessions/${data.session.id}`)).session;
      const players = (await api("/api/players?include_inactive=true")).players;
      const courts = (await api("/api/courts")).courts;
      const byId = new Map(players.map(player => [player.id, player]));
      const courtById = new Map(courts.map(court => [court.id, court]));
      const actions = document.querySelector(".bottom-actions");
      actions.replaceChildren();
      // Remove unimplemented export/demo controls rather than claiming they work.
      document.querySelector(".page-topline > button")?.remove();
      const round = session.rounds.at(-1);
      document.getElementById("sessionTitle").textContent = session.name;
      document.getElementById("sessionTimer").textContent = session.status;
      document.getElementById("playerCount").textContent = session.participants.length;
      document.getElementById("courtCount").textContent = session.court_ids.length;
      document.getElementById("currentRound").textContent = round?.number || "Not generated";
      const selector = document.querySelector(".rounds");
      selector.replaceChildren();
      let selectedRound = round;
      let final;
      function draw(current) {
    selectedRound = current;

    final = current
        ? structuredClone(current.final)
        : { assignments: [], sit_out_player_ids: [] };

    const matches = document.getElementById("matchGrid");
    const sitting = document.getElementById("sitoutList");

    matches.replaceChildren();
    sitting.replaceChildren();

    document.getElementById("currentRound").textContent =
        current?.number || "Not generated";

    document.getElementById("matchCount").textContent =
        final.assignments.length;

    document.getElementById("sitoutCount").textContent =
        final.sit_out_player_ids.length;

    const eligible = current?.eligibility
        .filter(item => item.status === "eligible")
        .map(item => item.player_id) || [];

    const editable = current?.status === "generated";

    // Compact player selector, preserving manual override.
    function playerSelect(parent, playerId, change) {
        const line = element("div", undefined, parent);
        line.className = "player-line";

        const player = byId.get(playerId);

        const genderDot = element("span", undefined, line);
        genderDot.className = `gender-dot ${genderDotClass(player?.gender)}`;
        genderDot.title =
            player?.gender === "F" ? "Female" :
            player?.gender === "M" ? "Male" : "Unknown";

        const select = element("select", undefined, line);
        select.className = "player-inline-select";

        for (const id of eligible) {
            const option = element(
                "option",
                byId.get(id)?.name || id,
                select
            );
            option.value = id;
        }

        select.value = playerId;
        select.disabled = !editable;

        select.onchange = () => change(select.value);
    }

    // Generate styled court cards.
    for (const assignment of final.assignments) {
        const card = element("div", undefined, matches);
        card.className = "card match-card";

        const header = element("div", undefined, card);
        header.className = "match-card-header";

        element(
            "span",
            `Court ${courtById.get(assignment.court_id)?.number || assignment.court_id}`,
            header
        );

        // Team A
        const teamA = element("div", undefined, card);
        teamA.className = "team";

        const labelA = element("div", "TEAM A", teamA);
        labelA.className = "team-label";

        assignment.team_a_player_ids.forEach((id, index) => {
            playerSelect(teamA, id, value => {
                assignment.team_a_player_ids[index] = value;
            });
        });

        // VS divider
        const vs = element("div", "VS", card);
        vs.className = "vs";

        // Team B
        const teamB = element("div", undefined, card);
        teamB.className = "team";

        const labelB = element("div", "TEAM B", teamB);
        labelB.className = "team-label";

        assignment.team_b_player_ids.forEach((id, index) => {
            playerSelect(teamB, id, value => {
                assignment.team_b_player_ids[index] = value;
            });
        });
    }

    // Styled sitting-out players
    final.sit_out_player_ids.forEach((id, index) => {
        const row = element("div", undefined, sitting);
        row.className = "sitout-player";

        const name = byId.get(id)?.name || id;

        const avatar = element(
            "div",
            name.split(" ").map(part => part[0]).slice(0, 2).join("").toUpperCase(),
            row
        );
        avatar.className = "player-avatar";

        const details = element("div", undefined, row);
        details.className = "sitout-details";

        playerSelect(details, id, value => {
            final.sit_out_player_ids[index] = value;
        });

        const description = element(
            "div",
            "Sitting out this round",
            details
        );
        description.className = "sitout-meta";
    });

    document.getElementById("matchExplanation").textContent =
        current?.status === "completed"
            ? "Completed round — locked."
            : "Change players if needed, then click Save Changes. Duplicate assignments are rejected by the backend.";
}
      for (const saved of session.rounds) {
        const roundButton = button(
            `Round ${saved.number}`,
            selector,
            () => draw(saved)
        );

        roundButton.className = "round-btn";

        if (saved.status === "completed") {
            roundButton.classList.add("completed");
        } else if (saved.id === round?.id) {
            roundButton.classList.add("current");
        }

        roundButton.replaceChildren();

        const dot = element(
            "span",
            saved.status === "completed" ? "✓" : String(saved.number),
            roundButton
        );
        dot.className = "round-dot";

        element("span", `Round ${saved.number}`, roundButton);
    }
      draw(round);

      // End the current session and move to History.
      const endSessionBtn = document.getElementById("endSessionBtn");

      if (endSessionBtn) {
          endSessionBtn.disabled =
              ["completed", "cancelled"].includes(session.status);

          endSessionBtn.onclick = async () => {
              // Prevent accidentally discarding manual player changes.
              if (
                  selectedRound?.status === "generated" &&
                  JSON.stringify(final) !== JSON.stringify(selectedRound.final)
              ) {
                  notice.textContent =
                      "Please save or undo your manual changes before ending the session.";
                  return;
              }

              const confirmed = window.confirm(
                  `Are you sure you want to end "${session.name}"?\n\n` +
                  "The session will be marked as completed and its saved matches will be preserved."
              );

              if (!confirmed) return;

              endSessionBtn.disabled = true;
              notice.textContent = "";

              try {
                  await api(
                      `/api/sessions/${session.id}/end`,
                      "POST"
                  );

                  // Move to History after successful completion.
                  window.location.assign("/history");

              } catch (error) {
                  showError(error);
                  endSessionBtn.disabled = false;
              }
          };
      }
      if (!round || round.status === "completed") {
        if (["draft", "active"].includes(session.status)) button("Generate Next Round", actions, async () => {
          await api(`/api/sessions/${session.id}/rounds/generate`, "POST"); location.reload();
        });
      }
      if (round?.status === "generated") {
        button("Undo Unsaved Changes", actions, () => draw(selectedRound));
        button("Save Changes", actions, async () => {
          const assignedIds = [
              ...final.assignments.flatMap(match => [
                  ...match.team_a_player_ids,
                  ...match.team_b_player_ids
              ]),
              ...final.sit_out_player_ids
          ];

          if (assignedIds.length !== new Set(assignedIds).size) {
              throw new Error(
                  "A player has been assigned more than once. Please check the teams and sitting-out list."
              );
          }
          if (selectedRound?.status !== "generated") throw new Error("Completed rounds are locked.");
          await api(`/api/rounds/${selectedRound.id}/final`, "PUT", {...final, expected_revision: selectedRound.override_revision});
          location.reload();
        });
        button("Complete Round", actions, async () => {
          if (selectedRound.id !== round.id) throw new Error("Select the current round first.");
          if (JSON.stringify(final) !== JSON.stringify(selectedRound.final)) throw new Error("Save or undo your changes before completing the round.");
          await api(`/api/rounds/${round.id}/complete`, "POST"); location.reload();
        });
      }
      if (["draft", "active"].includes(session.status)) {
        const late = element("select", undefined, actions); late.id = "late-player";
        const joined = new Set(session.participants.map(item => item.player_id));
        for (const player of players.filter(player => player.active && !joined.has(player.id))) {
          const option = element("option", player.name, late); option.value = player.id;
        }
        button("Add Late Player", actions, async () => {
          if (!late.value) throw new Error("Select a player who has not joined yet.");
          await api(`/api/sessions/${session.id}/players`, "POST", {player_id: late.value}); location.reload();
        });
        const leave = element("select", undefined, actions); leave.id = "unavailable-player";
        for (const participant of session.participants.filter(item => !item.unavailable_from_round)) {
          const option = element("option", byId.get(participant.player_id)?.name || participant.player_id, leave);
          option.value = participant.player_id;
        }
        button("Unavailable From Next Round", actions, async () => {
          if (!leave.value) throw new Error("Select a participant.");
          await api(`/api/sessions/${session.id}/players/${leave.value}`, "PATCH", {reason: "Unavailable for remainder of session"}); location.reload();
        });
      }
      if (new URLSearchParams(location.search).has("generation_failed")) notice.textContent = "Session saved. Generation failed; retry Generate Next Round to see the error.";
    }
  } catch (error) { showError(error); }
})();
