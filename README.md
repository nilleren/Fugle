# Fuglestation

Et lille Python-projekt til at registrere fuglelyde med en mikrofon.

Projektet bygges trin for trin. Lige nu kan programmet:

- finde tilgaengelige mikrofoner
- vaelge mikrofon automatisk fra `config.toml`
- optage lyd
- gemme optagelsen som WAV
- analysere en WAV-fil med BirdNET
- gemme BirdNET-resultater som CSV
- gemme BirdNET-resultater i SQLite
- vise seneste detektioner i terminalen
- vise seneste detektioner paa en lokal webside
- vise den aktuelle konfiguration paa websiden
- vise tilgaengelige mikrofoner paa websiden
- lave en kort testoptagelse fra websiden
- gemme valgt mikrofon fra websiden
- gemme optagelaengde og registrerings-confidence fra websiden
- gemme natpause fra websiden
- vise kendte fuglearter med dansk og latinsk navn

Websiden laeser fra SQLite og viser de seneste detektioner.
Der findes ogsaa en separat vaegvisning, som er taenkt til en skaerm eller
digital ramme.

## Krav

- Windows-pc
- USB-mikrofon
- Python 3.11 eller nyere

## Opsaetning

Opret virtuelt miljoe:

```powershell
python -m venv .venv
```

Aktiver miljoeet:

```powershell
.\.venv\Scripts\Activate.ps1
```

Installer afhaengigheder:

```powershell
python -m pip install -r requirements.txt
python -m pip install -e .
```

## Hemmeligheder

Lokale API-noegler gemmes i `.env`, som ikke maa committes til Git.

Opret filen saadan:

```powershell
Copy-Item .env.example .env
```

Ret derefter:

```text
EBIRD_API_KEY=din-ebird-api-key
OPENAI_API_KEY=din-openai-api-key
```

## Konfiguration

Programmet laeser som standard `config.toml`:

```toml
[site]
title = "Fuglene i haven"

[audio]
device = 0
duration_seconds = 10
sample_rate = 44100
output_dir = "recordings"

[birdnet]
use_geo = true
latitude = 56.0
longitude = 10.0
week = 0
min_confidence = 0.1
geo_min_confidence = 0.05

[database]
path = "data/fuglestation.db"

[schedule]
first_phase_seconds = 120
first_phase_interval_seconds = 30
second_phase_seconds = 600
second_phase_interval_seconds = 60
steady_interval_seconds = 900
quiet_start = "22:00"
quiet_end = "05:00"

[wall]
min_confidence = 0.5
max_species = 20
recent_minutes = 1400
show_names = true
size_mode = "common"
show_footer = false
show_latin_names = false
show_shadows = true
```

Projektets default-konfiguration ligger i:

```text
config.default.toml
```

Dashboardet har en knap til at nulstille `config.toml` til denne default.
`site.title` styrer overskriften paa vaegvisning og statistik, og kan rettes
fra dashboardets indstillinger.

`device = 0` betyder Windows' standard-input. Hvis du vil bruge en bestemt
USB-mikrofon, saa find dens nummer med `--list` og ret `device`.

BirdNET bruger som standard Danmarks omtrentlige centrum:

- `latitude = 56.0`
- `longitude = 10.0`

`week = 0` betyder, at programmet bruger den aktuelle ISO-uge automatisk.
Hvis fuglestationen senere faar en fast placering, kan koordinaterne rettes i
`config.toml`.

## Optag lyd

Vis tilgaengelige mikrofoner:

```powershell
python -m fuglestation.record_audio --list
```

Optag med indstillingerne fra `config.toml`:

```powershell
python -m fuglestation.record_audio
```

Overstyr konfigurationen fra kommandolinjen:

```powershell
python -m fuglestation.record_audio --device 0 --duration 10 --sample-rate 44100
```

Optagelser gemmes i mappen `recordings`.
Efter hver ny optagelse beholdes kun de 3 nyeste WAV-filer i optagemappen.

## Fuld cyklus

Optag en lydfil, analyser den med BirdNET og gem resultatet i SQLite:

```powershell
python -m fuglestation.run_cycle
```

Til en hurtig test kan du optage kortere:

```powershell
python -m fuglestation.run_cycle --duration 2
```

Websiden opdaterer automatisk bagefter, hvis serveren koerer.

## Kontinuerlig drift

Koer fuglestationen efter tidsplanen:

```powershell
python -m fuglestation.run_scheduler
```

Tidsplanen er:

- optag straks ved start
- derefter hvert 30. sekund de foerste 2 minutter
- derefter hvert minut de naeste 10 minutter
- derefter hvert kvarter
- ingen optagelser mellem 22:00 og 05:00

Til en hurtig test uden optagelse:

```powershell
python -m fuglestation.run_scheduler --dry-run --ignore-quiet-hours --max-cycles 1
```

Stop med `Ctrl+C`.

## BirdNET-analyse

Analyser den nyeste WAV-fil i `recordings`:

```powershell
python -m fuglestation.analyze_audio
```

Som standard bruger analysen geografi fra `config.toml`, saa BirdNET kun
prioriterer arter der er sandsynlige i Danmark paa den aktuelle tid af aaret.

Analyser en bestemt WAV-fil:

```powershell
python -m fuglestation.analyze_audio recordings\recording-20260714-211727.wav
```

Resultater gemmes som CSV i `analysis_results` og i SQLite-databasen fra
`config.toml`.

Du kan saenke eller haeve graensen for detektioner:

```powershell
python -m fuglestation.analyze_audio --confidence 0.05
```

Du kan analysere uden geografisk filter:

```powershell
python -m fuglestation.analyze_audio --no-geo
```

Du kan analysere uden at gemme i SQLite:

```powershell
python -m fuglestation.analyze_audio --no-db
```

Du kan ogsaa overstyre geografien midlertidigt:

```powershell
python -m fuglestation.analyze_audio --latitude 55.68 --longitude 12.57 --week 29
```

Foerste BirdNET-koersel kan tage laengere tid, fordi modellen hentes automatisk.

## SQLite

Databasen ligger som standard her:

```text
data/fuglestation.db
```

Vis de seneste detektioner:

```powershell
python -m fuglestation.list_detections
```

Vis fx de seneste 20:

```powershell
python -m fuglestation.list_detections --limit 20
```

## eBird

Projektet kan laese en eBird API-noegle fra `.env`:

```text
EBIRD_API_KEY=din-ebird-api-key
```

Test forbindelsen til eBird:

```powershell
python -m fuglestation.ebird --region DK --max-results 5
```

Hvis PowerShell rammer WindowsApps Python i stedet for det virtuelle miljoe,
saa brug den direkte:

```powershell
.\.venv\Scripts\python.exe -m fuglestation.ebird --region DK --max-results 5
```

eBird-kald bruger headeren `X-eBirdApiToken`.

## Webside

Start den lokale webserver:

```powershell
python -m uvicorn fuglestation.web:app --host 127.0.0.1 --port 8000
```

Aabn vaegvisningen:

```text
http://127.0.0.1:8000
```

Aabn dashboardet med indstillinger:

```text
http://127.0.0.1:8000/settings/
```

Aabn mobilstatistikken:

```text
http://127.0.0.1:8000/stats
```

Websiden viser de seneste detektioner fra SQLite. API'et kan ogsaa kaldes
direkte:

```text
http://127.0.0.1:8000/api/detections
```

Websiden har:

- driftsstatus fra scheduleren
- start/stop af scheduleren
- visning af den aktuelle `config.toml`
- visning af tilgaengelige mikrofoner
- knap til kort testoptagelse med valgt mikrofon
- knap til at gemme valgt mikrofon i `config.toml`
- felter til optagelaengde, registrerings-confidence og natpause
- felter til maks antal fugle og tidsvindue for vægvisningen
- artssammendrag
- filter for minimum confidence
- automatisk opdatering hvert 10. sekund

API'et kan filtrere paa confidence:

```text
http://127.0.0.1:8000/api/detections?min_confidence=0.8
```

Vaegvisningen bruger et kompakt API:

```text
http://127.0.0.1:8000/api/wall
```

Mobilstatistikken bruger et separat API:

```text
http://127.0.0.1:8000/api/stats
```

Statistiksiden viser totaler, doegnrytme, hyppigste arter og en lille
artsvisning med fund pr. time og dag. Den er optimeret til mobil og bruger
samme varme visuelle stil som vaegvisningen.

Fra listen over hyppigste fugle kan man trykke paa en art og aabne en
artsside, fx:

```text
http://127.0.0.1:8000/stats/species?species_name=Turdus%20merula_Eurasian%20Blackbird
```

Artssiden viser billede, dansk og latinsk navn, fund, optagelser, foerste og
seneste optagelse samt fordelinger hen over doegnets timer og aarets maaneder.

`wall.size_mode` bestemmer fuglenes relative stoerrelse paa vaeggen:

- `equal`: alle fugle vises omtrent ens
- `common`: arter med flest fund vises stoerst
- `rare`: arter med faerrest fund vises stoerst

Vaegvisningen henter nye data med JavaScript uden at reloade hele siden. Fuglene
tegnes kun om, naar arterne eller indstillingerne aendrer sig, eller naar
hyppigheden forskyder sig tydeligt. Kl. 03:00 reloader siden helt, saa
tilfaeldige billedvarianter og placeringer kan skifte roligt.

Lokale fuglebilleder kan laegges i:

```text
assets/birds
```

Filnavnet skal svare til BirdNET-artnavnet, hvor tegn der ikke er bogstaver
eller tal bliver til `_`. Eksempel:

```text
Turdus_merula_Eurasian_Blackbird.png
```

Hvis du har flere billeder af samme fugl, kan du tilfoeje et tal direkte efter
navnet. Vaegvisningen vaelger saa en tilfaeldig variant, hver gang data hentes:

```text
Turdus_merula_Eurasian_Blackbird.png
Turdus_merula_Eurasian_Blackbird2.png
```

Hvis billedet findes, bruger vaegvisningen det. Hvis det mangler, vises den
grafiske fallback stadig.

### Klip fugle fri fra baggrunden

Projektet har et separat vaerktoej til at fjerne baggrunden fra lokale
fuglebilleder. Det bruger `rembg` lokalt paa maskinen og gemmer billedet som
transparent PNG samme sted.

Installer de ekstra billed-afhaengigheder:

```powershell
python -m pip install -r requirements-cutout.txt
```

Klip et bestemt billede:

```powershell
python -m fuglestation.cutout_birds Pica_pica_Eurasian_Magpie.png
```

Klip alle PNG-billeder i `assets/birds`:

```powershell
python -m fuglestation.cutout_birds
```

Foerste koersel kan tage laengere tid, fordi `rembg` henter modellen til den
lokale maskine.

Vaegvisningen bruger ogsaa smaa alpha-masker til at placere fuglene efter
deres omrids i stedet for efter billedets firkant. Genbyg maskerne, naar du
har tilfoejet eller aendret fuglebilleder:

```powershell
python -m fuglestation.build_bird_masks
```

### Generer fuglebilleder med OpenAI

Projektet har et separat forberedelses-script, som kan generere lokale
fuglebilleder i to versioner pr. art. Det koeres manuelt, naar du vil udvide
billedbiblioteket.

Foerst skal `.env` indeholde:

```text
OPENAI_API_KEY=din-openai-api-key
```

Se hvad scriptet vil lave uden at kalde OpenAI:

```powershell
python -m fuglestation.generate_bird_images --dry-run --limit 3
```

Brug projektets faste prompt:

```powershell
python -m fuglestation.generate_bird_images --prompt-file prompts\bird_image_prompt.txt --dry-run --limit 3
```

Generer billeder for de foerste 3 arter i kandidatlistefilen:

```powershell
python -m fuglestation.generate_bird_images --prompt-file prompts\bird_image_prompt.txt --limit 3
```

Scriptet gemmer billeder i `assets/birds` med samme navngivning som
vaegvisningen bruger, fx:

```text
Turdus_merula_Eurasian_Blackbird.png
Turdus_merula_Eurasian_Blackbird2.png
```

Kandidatlistefilen ligger her:

```text
assets/bird_image_candidates.json
```

Prompten ligger her og kan justeres uden at aendre Python-koden:

```text
prompts/bird_image_prompt.txt
```

Prompten bruger `{pose}` og `{pose_instruction}`, saa version 1 bliver en
siddende eller staaende profil med foldede vinger, mens version 2 bliver en
fugl i flugt.

Scriptet kan ogsaa bruge arter fra databasen:

```powershell
python -m fuglestation.generate_bird_images --source database --dry-run
```

Eller hente mulige arter fra eBird omkring koordinaterne i `config.toml`:

```powershell
python -m fuglestation.generate_bird_images --source ebird --ebird-radius-km 25 --ebird-back-days 30 --dry-run
```

Byg en helarsliste fra BirdNETs geografimodel og se hvilke arter der mangler
billeder:

```powershell
python -m fuglestation.build_year_round_candidates
```

Scriptet skriver lokale filer i `data/`:

```text
data/birdnet_year_round_candidates.json
data/birdnet_year_round_missing_images.json
```

Mangellisten kan bruges direkte som kandidatliste til billedgenerering:

```powershell
python -m fuglestation.generate_bird_images --candidates data\birdnet_year_round_missing_images.json --prompt-file prompts\bird_image_prompt.txt --dry-run
```

Naar billederne er genereret, kan de klippes fri og maskerne genbygges:

```powershell
python -m fuglestation.cutout_birds
python -m fuglestation.build_bird_masks
```

Kendte arter vises med dansk og latinsk navn, fx:

```text
Solsort / Turdus merula
```

Danske artsnavne vedligeholdes i:

```text
src/fuglestation/danish_species_names.json
```

Listen kan opdateres fra eBird, naar `.env` indeholder `EBIRD_API_KEY`:

```powershell
python -m fuglestation.update_danish_species_names
```

Scheduler-status kan ogsaa laeses direkte:

```text
http://127.0.0.1:8000/api/status
```

Konfigurationen kan laeses direkte:

```text
http://127.0.0.1:8000/api/config
```

Tilgaengelige mikrofoner kan laeses direkte:

```text
http://127.0.0.1:8000/api/audio/devices
```

En kort testoptagelse kan startes fra websiden eller API'et:

```text
POST http://127.0.0.1:8000/api/audio/test-recording
```

De seneste fulde WAV-optagelser og de seneste artsklip kan hoeres fra settings-siden eller API'et:

```text
GET http://127.0.0.1:8000/api/audio/recordings
GET http://127.0.0.1:8000/api/audio/species-clips
```

Valgt mikrofon kan gemmes fra websiden eller API'et:

```text
POST http://127.0.0.1:8000/api/config/audio-device
```

Sidetitel, optagelaengde, registrerings-confidence, natpause og vaegvisning kan gemmes fra websiden eller API'et:

```text
POST http://127.0.0.1:8000/api/config/runtime-settings
```

Indstillingerne kan nulstilles til `config.default.toml`:

```text
POST http://127.0.0.1:8000/api/config/reset-defaults
```

Scheduleren skriver runtime-status til:

```text
data/status.json
```

Schedulerens terminaloutput skrives til:

```text
data/scheduler.log
```

Start/stop kan ogsaa kaldes som API:

```text
POST http://127.0.0.1:8000/api/scheduler/start
POST http://127.0.0.1:8000/api/scheduler/stop
```

## Nuvaerende fase

- BirdNET kan analysere en WAV-fil.
- BirdNET bruger Danmark som geografisk filter.
- BirdNET-resultater gemmes i SQLite.
- FastAPI-webserver viser seneste detektioner.
- En fuld enkeltcyklus kan optage, analysere og gemme.
- Kontinuerlig drift kan koere efter tidsplan med natpause.
- Websiden viser schedulerens driftsstatus.
- Websiden kan starte og stoppe scheduleren lokalt.
- Websiden viser den aktuelle konfiguration fra `config.toml`.
- Websiden viser tilgaengelige mikrofoner.
- Websiden kan lave en kort testoptagelse med den valgte mikrofon.
- Websiden kan afspille de seneste fulde optagelser.
- Websiden kan afspille seneste gemte lydklip for hver hoert art.
- Websiden kan gemme valgt mikrofon i `config.toml`.
- Websiden kan gemme optagelaengde og registrerings-confidence i `config.toml`.
- Websiden kan gemme en separat confidence-graense for vaegvisningen i `config.toml`.
- Websiden kan gemme natpause i `config.toml`.
- Vaegvisningen viser en rolig fuglecollage baseret paa SQLite-data.
- Vaegvisningen kan bruge lokale fuglebilleder fra `assets/birds`.
