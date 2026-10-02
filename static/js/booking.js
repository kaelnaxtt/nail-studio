document.addEventListener('DOMContentLoaded', function () {
  const form = document.getElementById('bookingForm');
  if (!form) return;

  const steps = Array.from(document.querySelectorAll('.wiz-step'));
  const dots = Array.from(document.querySelectorAll('.step-dot'));
  let current = 1;
  const total = steps.length;

  const dateInput = document.getElementById('dateInput');
  const calendar = document.getElementById('bookingCalendar');
  const slotWrap = document.getElementById('slotWrap');
  const timeHidden = document.getElementById('timeHidden');
  const nextBtn = document.getElementById('wizNext');
  const backBtn = document.getElementById('wizBack');
  const submitBtn = document.getElementById('wizSubmit');
  const summaryBox = document.getElementById('summaryBox');

  function showStep(n) {
    current = n;
    steps.forEach((s, i) => s.classList.toggle('active', i === n - 1));
    dots.forEach((d, i) => {
      d.classList.toggle('active', i === n - 1);
      d.classList.toggle('done', i < n - 1);
    });
    backBtn.style.visibility = n === 1 ? 'hidden' : 'visible';
    nextBtn.style.display = n < total ? 'inline-flex' : 'none';
    submitBtn.style.display = n === total ? 'inline-flex' : 'none';
    if (n === total) buildSummary();
    validateStep();
    window.scrollTo({ top: form.offsetTop - 90, behavior: 'smooth' });
  }

  function validateStep() {
    let ok = true;
    if (current === 1) {
      ok = !!form.querySelector('input[name="service_id"]:checked');
    } else if (current === 2) {
      ok = !!dateInput.value;
    } else if (current === 3) {
      ok = !!timeHidden.value;
    } else if (current === 4) {
      const name = document.getElementById('nameInput').value.trim();
      const phone = document.getElementById('phoneInput').value.replace(/\D/g, '');
      ok = name.length > 1 && phone.length >= 7;
    }
    nextBtn.disabled = !ok;
  }

  form.addEventListener('input', validateStep);
  form.addEventListener('change', validateStep);

  nextBtn.addEventListener('click', () => { if (current < total) showStep(current + 1); });
  backBtn.addEventListener('click', () => { if (current > 1) showStep(current - 1); });

  if (dateInput && calendar) {
    const today = calendar.dataset.today;
    const todayDate = new Date(`${today}T00:00:00`);
    let shownYear = todayDate.getFullYear();
    let shownMonth = todayDate.getMonth() + 1;
    const calendarTitle = document.getElementById('calendarTitle');
    const calendarDays = document.getElementById('calendarDays');
    const calendarHint = document.getElementById('calendarHint');
    const prevMonth = document.getElementById('calendarPrev');
    const nextMonth = document.getElementById('calendarNext');
    const monthName = new Intl.DateTimeFormat('es-CO', { month: 'long', year: 'numeric' });

    function renderCalendar() {
      calendarTitle.textContent = monthName.format(new Date(shownYear, shownMonth - 1, 1));
      prevMonth.disabled = shownYear === todayDate.getFullYear() && shownMonth === todayDate.getMonth() + 1;
      calendarDays.innerHTML = '<p class="help-text">Consultando horarios…</p>';
      fetch(`/api/disponibilidad/mes?anio=${shownYear}&mes=${shownMonth}`)
        .then(response => { if (!response.ok) throw new Error('No disponible'); return response.json(); })
        .then(data => {
          const firstWeekday = (new Date(shownYear, shownMonth - 1, 1).getDay() + 6) % 7;
          const cells = Array(firstWeekday).fill('<span class="calendar-blank" aria-hidden="true"></span>');
          data.days.forEach(day => {
            const takenCount = Number(day.taken || 0);
            const stateLabel = day.state === 'available'
              ? `${day.available} horarios libres${takenCount ? ` y ${takenCount} ocupados` : ''}`
              : (day.state === 'full' ? `Agenda llena, ${takenCount} horarios ocupados` : 'Día cerrado o pasado');
            const selected = dateInput.value === day.date;
            const isToday = day.date === today;
            const disabled = day.state !== 'available';
            const occupancy = day.state === 'available' && takenCount
              ? `<em class="occupied-count">${takenCount} ocup.</em>`
              : '';
            const availability = day.state === 'available'
              ? `<small class="availability-count">${day.available} libres</small>${occupancy}`
              : (day.state === 'full' ? '<small class="availability-count">Agenda llena</small>' : '<small class="availability-count">Cerrado</small>');
            cells.push(`<button type="button" class="calendar-day ${day.state}${takenCount ? ' has-bookings' : ''}${selected ? ' selected' : ''}${isToday ? ' today' : ''}" data-date="${day.date}" title="${stateLabel}" ${disabled ? 'disabled' : ''} aria-label="${day.date}, ${stateLabel}" ${selected ? 'aria-pressed="true"' : 'aria-pressed="false"'}><span>${Number(day.date.slice(-2))}</span>${availability}</button>`);
          });
          calendarDays.innerHTML = cells.join('');
          calendarDays.querySelectorAll('.calendar-day:not(:disabled)').forEach(button => {
            button.addEventListener('click', () => {
              dateInput.value = button.dataset.date;
              timeHidden.value = '';
              renderCalendar();
              loadSlots(dateInput.value);
              calendarHint.textContent = `Fecha elegida: ${button.dataset.date}. Las horas ocupadas aparecen desactivadas.`;
              validateStep();
            });
          });
        })
        .catch(() => { calendarDays.innerHTML = '<p class="help-text">No se pudo consultar el calendario.</p>'; });
    }

    prevMonth.addEventListener('click', () => {
      if (shownMonth === 1) { shownMonth = 12; shownYear--; } else shownMonth--;
      renderCalendar();
    });
    nextMonth.addEventListener('click', () => {
      if (shownMonth === 12) { shownMonth = 1; shownYear++; } else shownMonth++;
      renderCalendar();
    });
    renderCalendar();
  }

  function loadSlots(fecha) {
    slotWrap.innerHTML = '<p class="help-text">Cargando horarios…</p>';
    fetch('/api/disponibilidad?fecha=' + encodeURIComponent(fecha))
      .then(r => r.json())
      .then(data => {
        if (!data.slots || data.slots.length === 0) {
          slotWrap.innerHTML = '<div class="empty-state">No hay horarios disponibles para este día.</div>';
          return;
        }
        slotWrap.innerHTML = data.slots.map(s => `
          <label class="slot ${s.taken ? 'taken' : ''}">
            <input type="radio" name="time_radio" value="${s.time}" ${s.taken ? 'disabled' : ''}>
            <span>${s.time}${s.taken ? `<small class="slot-state">${s.reason === 'booked' ? 'Ocupada' : 'No disponible'}</small>` : ''}</span>
          </label>`).join('');
        slotWrap.querySelectorAll('input[name="time_radio"]').forEach(r => {
          r.addEventListener('change', () => { timeHidden.value = r.value; validateStep(); });
        });
      })
      .catch(() => { slotWrap.innerHTML = '<div class="empty-state">No se pudo cargar la disponibilidad.</div>'; });
  }

  function buildSummary() {
    const svc = form.querySelector('input[name="service_id"]:checked');
    const svcLabel = svc ? svc.closest('label').dataset.label : '';
    const svcPrice = svc ? Number(svc.closest('label').dataset.price || 0) : 0;
    const addons = Array.from(form.querySelectorAll('input[name="add_ons"]:checked'));
    const addonTotal = addons.reduce((a, el) => a + Number(el.dataset.price || 0), 0);
    const addonNames = addons.map(el => el.dataset.name).join(', ') || 'Ninguno';
    const name = document.getElementById('nameInput').value.trim();
    const phone = document.getElementById('phoneInput').value.trim();
    const obs = document.getElementById('obsInput').value.trim() || '—';
    const fecha = dateInput.value;
    const hora = timeHidden.value;
    const total = svcPrice + addonTotal;
    summaryBox.innerHTML = `
      <div class="summary-row"><span>Servicio</span><b>${svcLabel}</b></div>
      <div class="summary-row"><span>Fecha</span><b>${fecha}</b></div>
      <div class="summary-row"><span>Hora</span><b>${hora}</b></div>
      <div class="summary-row"><span>Nombre</span><b>${name}</b></div>
      <div class="summary-row"><span>Teléfono</span><b>${phone}</b></div>
      <div class="summary-row"><span>Adicionales</span><b>${addonNames}</b></div>
      <div class="summary-row"><span>Observaciones</span><b>${obs}</b></div>
      <div class="summary-row" style="border-bottom:none;"><span>Total estimado</span><b>$${total.toLocaleString('es-CO')}</b></div>
    `;
  }

  // preselect from ?service=ID
  const pre = form.dataset.preselect;
  if (pre) {
    const el = form.querySelector('input[name="service_id"][value="' + pre + '"]');
    if (el) { el.checked = true; showStep(2); } else { showStep(1); }
  } else {
    showStep(1);
  }
});

// mobile nav toggle (shared)
document.addEventListener('DOMContentLoaded', function () {
  const btn = document.getElementById('menuBtn'), navEl = document.getElementById('mainNav'), ov = document.getElementById('overlay');
  if (btn) btn.onclick = () => { navEl.classList.toggle('open'); ov.classList.toggle('show'); };
  if (ov) ov.onclick = () => { navEl.classList.remove('open'); ov.classList.remove('show'); };

  // auto-hide flash messages
  document.querySelectorAll('.flash').forEach(f => {
    setTimeout(() => { f.style.transition = 'opacity .4s'; f.style.opacity = '0'; }, 4000);
  });
});
