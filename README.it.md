🇬🇧 [Read in English](README.md)

# Runic.io per Home Assistant

Integrazione personalizzata per i localizzatori GPS [Runic.io](https://www.runic.io).

- I **veicoli** vengono letti dall'API REST di Runic (`https://api.runic.io/v1`, token Bearer) ogni 5 minuti
  e subito dopo ogni avviso. Per ogni veicolo ottieni:
  - `device_tracker.<veicolo>`: la posizione sulla mappa.
  - `sensor.<veicolo>_speed`: la velocità GPS dell'ultima rilevazione, in km/h.
  - `binary_sensor.<veicolo>_moving`: acceso quando l'ultima rilevazione ha meno di 15 minuti ed è a 5 km/h o più.
    Lo stato `RUN`/`STOP` di Runic viene conservato solo come attributo `runic_status`: indica `RUN` ogni volta
    che il localizzatore è attivo (heartbeat, accensione), anche quando il veicolo è parcheggiato.
- Gli **avvisi** arrivano all'istante come webhook Runic firmati e vengono pubblicati sul bus degli eventi di HA come `runic_event`.
  Vengono verificati le firme (Standard Webhooks, HMAC-SHA256), i timestamp e le consegne duplicate.

## Installazione

### HACS (consigliato)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=martineo14&repository=ha-runic&category=integration)

Oppure in HACS → ⋮ → **Repository personalizzati**, aggiungi `https://github.com/martineo14/ha-runic` come *Integrazione*,
poi scarica **Runic.io** e riavvia Home Assistant.

### Manuale

Copia `custom_components/runic` nella cartella `config/custom_components/` di HA e riavvia.

### Configurazione

1. Impostazioni → Dispositivi e servizi → Aggiungi integrazione → **Runic.io**.
2. Incolla il tuo token API (rilasciato dal supporto Runic, runic@runic.io).
3. Aggiungi l'URL del webhook mostrato nel [portale Runic](https://developers.runic.io/portal/webhooks),
   scegli i tipi di avviso e incolla di nuovo in HA il segreto di firma `whsec_…`.

HA deve essere raggiungibile da internet in HTTPS (Nabu Casa, Cloudflare Tunnel, reverse proxy)
e l'URL non deve fare redirect: Runic non segue i redirect.

## Automazioni

### Dall'editor delle automazioni

Ogni veicolo ha un'attivazione per ogni tipo di avviso Runic. Aggiungi attivazione → **Per destinazione** (By target) → scegli il veicolo
(per esempio *Camion 3*) → scegli l'avviso: *Allarme furto*, *Partito*, *Entrato in un punto di interesse*,
*Uscito da un punto di interesse*, *Limite di velocità superato*, *Batteria del veicolo scarica*… Oppure usa **Dispositivo** → il veicolo → l'avviso.

Ogni veicolo ha anche un'entità `event.<veicolo>_last_alert` (*Ultimo avviso*) che mostra il suo avviso più recente nella dashboard,
nella cronologia e nel registro.

Un tipo di avviso scatta solo se in Runic sono configurate due cose: il tipo è selezionato sul tuo endpoint webhook
(developers.runic.io → Webhooks) ed esiste una regola di avviso per quel tipo nell'app Runic.

### In YAML

```yaml
triggers:
  - trigger: event
    event_type: runic_event
    event_data:
      type: alert.poi_area
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        {{ trigger.event.data.vehicle_name }}
        {{ 'è arrivato al' if trigger.event.data.data.direction == 'in' else 'ha lasciato il' }}
        punto di interesse {{ trigger.event.data.data.poi_id }}
```

Dati dell'evento: `id`, `type`, `alert` (il tipo senza `alert.`), `device_id`, `direction` (geofence), `level`, `created_at`, `vehicle_id`, `vehicle_name`, `plate`,
`position` (`lat`, `lon`, `at`), `data` (specifici del tipo), `message`.

Tipi di avviso: `alert.movement`, `alert.poi_area`, `alert.speed_limit`, `alert.theft`, `alert.tow`,
`alert.parking_moved`, `alert.panic_button`, `alert.accident`, `alert.jamming`, `alert.cable_cut`,
`alert.tamper`, `alert.input_activation`, `alert.device_alarm`, `alert.low_battery`,
`alert.low_battery_internal`, `alert.offline`, `alert.fuel`, `alert.fuel_tank`, `alert.maintenance`,
`alert.test`. Un avviso scatta solo se esiste una regola di avviso per quel tipo nell'app Runic.

## Sviluppo

```bash
python -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest
```
