#!/usr/bin/env python3
"""Descarga datos reales de TMDb; reanudable y sin dependencias adicionales."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.themoviedb.org/3'
TYPE_NAMES = {2: 'theatrical_limited', 3: 'theatrical'}
AUTH_NAMES = ('TMDB_TOKEN', 'TMDB_ACCESS_TOKEN', 'TMDB_BEARER_TOKEN', 'TMDB_API_KEY')
COLUMNS = [
    'tmdb_id', 'title', 'release_date', 'release_type', 'release_country',
    'primary_release_date', 'release_date_us', 'reference_market', 'budget_usd',
    'genres', 'runtime_minutes', 'original_language', 'production_countries',
    'production_company_ids', 'director_ids', 'cast_ids', 'collection_id',
    'is_sequel', 'sequel_inference', 'age_certification', 'revenue_worldwide_usd',
    'revenue_observed_at', 'outcome_complete', 'completion_basis', 'label_status',
    'success', 'eligible_for_training', 'exclusion_reason', 'collected_at',
    'features_as_of', 'pre_release_features_verified', 'source_url', 'split',
    'tmdb_status',
]


class DownloadError(RuntimeError):
    """El mensaje no incluye URLs autenticadas ni credenciales."""


def local_today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).date()


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def configured_credentials():
    values = {name: os.environ.get(name, '').strip() for name in AUTH_NAMES}
    # Las variables de usuario creadas después de abrir Codex no se heredan
    # automáticamente; se consultan por nombre sin mostrarlas en pantalla.
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                for name in AUTH_NAMES:
                    if not values[name]:
                        try:
                            values[name] = str(winreg.QueryValueEx(key, name)[0]).strip()
                        except FileNotFoundError:
                            pass
        except OSError:
            pass
    for name in AUTH_NAMES[:-1]:
        if values[name]:
            return 'token', values[name].removeprefix('Bearer ').strip()
    if values['TMDB_API_KEY']:
        return 'key', values['TMDB_API_KEY']
    return None, None


class Client:
    def __init__(self, credential_type, credential, rate=8.0, timeout=25, retries=5):
        if not credential:
            raise DownloadError('Falta una credencial de TMDb.')
        self.credential_type = credential_type
        self.credential = credential
        self.interval = 1.0 / rate
        self.timeout, self.retries = timeout, retries
        self.lock = threading.Lock()
        self.last = 0.0
        self.stop = threading.Event()
        self.local = threading.local()

    def sleep(self, seconds):
        if self.stop.wait(max(0.0, seconds)):
            raise DownloadError('Descarga interrumpida.')

    def throttle(self):
        with self.lock:
            self.sleep(self.last + self.interval - time.monotonic())
            self.last = time.monotonic()

    def get(self, path, params=None):
        query = dict(params or {})
        headers = {'Accept': 'application/json', 'User-Agent': 'ML-Bloque2-dataset/2.0'}
        if self.credential_type == 'token':
            headers['Authorization'] = 'Bearer ' + self.credential
        else:
            query['api_key'] = self.credential
        url = API + path + ('?' + urllib.parse.urlencode(query) if query else '')
        if not hasattr(self.local, 'opener'):
            self.local.opener = urllib.request.build_opener()
        for attempt in range(self.retries):
            if self.stop.is_set():
                raise DownloadError('Descarga interrumpida.')
            self.throttle()
            try:
                request = urllib.request.Request(url, headers=headers)
                with self.local.opener.open(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                status = error.code
                if status == 404:
                    return None
                if status in (401, 403):
                    raise DownloadError(f'TMDb rechazó la autenticación o acceso (HTTP {status}).') from None
                if status != 429 and status < 500:
                    raise DownloadError(f'TMDb devolvió HTTP {status} en {path}.') from None
                retry_after = error.headers.get('Retry-After', '')
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    from email.utils import parsedate_to_datetime
                    try:
                        delay = max(0, (parsedate_to_datetime(retry_after) - dt.datetime.now(dt.timezone.utc)).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        delay = 2 ** attempt
                if attempt + 1 < self.retries:
                    self.sleep(delay)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                if attempt + 1 < self.retries:
                    self.sleep(2 ** attempt)
        raise DownloadError(f'No se pudo descargar {path} después de {self.retries} intentos.') from None


def month_windows(start, end):
    day = start.replace(day=1)
    while day <= end:
        following = (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(day, start), min(following - dt.timedelta(days=1), end)
        day = following


def discover_window(client, start, end):
    params = {'with_release_type': '2|3', 'release_date.gte': start.isoformat(),
              'release_date.lte': end.isoformat(), 'include_adult': 'false',
              'include_video': 'false', 'sort_by': 'primary_release_date.asc'}
    first = client.get('/discover/movie', {**params, 'page': 1})
    if first is None:
        raise DownloadError('No se recibió la página de descubrimiento.')
    pages = int(first.get('total_pages', 0))
    if pages > 500 or int(first.get('total_results', 0)) > 10_000:
        if start == end:
            raise DownloadError(f'La fecha {start} supera la paginación de TMDb; no se truncaron los resultados.')
        middle = start + (end-start)//2
        return discover_window(client, start, middle) | discover_window(client, middle+dt.timedelta(days=1), end)
    ids = {int(movie['id']) for movie in first.get('results', [])}
    for page in range(2, pages+1):
        result = client.get('/discover/movie', {**params, 'page': page})
        if result is None:
            raise DownloadError(f'Falta la página {page} de {start} a {end}.')
        ids.update(int(movie['id']) for movie in result.get('results', []))
    return ids


def parse_date(value):
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def pick_release(payload):
    records = []
    for country in (payload or {}).get('results', []):
        code = country.get('iso_3166_1', '')
        for entry in country.get('release_dates', []):
            date = parse_date(entry.get('release_date'))
            if entry.get('type') in TYPE_NAMES and date:
                records.append((date, code, entry['type'], (entry.get('certification') or '').strip()))
    if not records:
        return None
    records.sort()
    chosen = records[0]  # Una regla mundial uniforme, sin cambiarla según exista estreno estadounidense.
    us = [record for record in records if record[1] == 'US']
    return {'release_date': chosen[0].isoformat(), 'release_country': chosen[1],
            'release_type': TYPE_NAMES[chosen[2]],
            'age_certification': f'{chosen[1]}:{chosen[3]}' if chosen[3] else '',
            'release_date_us': us[0][0].isoformat() if us else ''}


def parse_movie(payload, collected_at):
    release = pick_release(payload.get('release_dates'))
    movie_id = int(payload['id'])
    if release is None:
        return {'tmdb_id': movie_id, 'skip_reason': 'no_theatrical_release', 'collected_at': collected_at}
    credits = payload.get('credits') or {}
    cast = sorted(credits.get('cast') or [], key=lambda person: person.get('order', 999999))
    collection = payload.get('belongs_to_collection') or {}
    return {
        'tmdb_id': movie_id, 'title': payload.get('title') or '', **release,
        'primary_release_date': payload.get('release_date') or '',
        'reference_market': 'WORLDWIDE', 'budget_usd': int(payload.get('budget') or 0),
        'genres': '|'.join(genre['name'] for genre in payload.get('genres') or []),
        'runtime_minutes': payload.get('runtime') or '',
        'original_language': payload.get('original_language') or '',
        'production_countries': '|'.join(country['iso_3166_1'] for country in payload.get('production_countries') or []),
        'production_company_ids': '|'.join(str(company['id']) for company in payload.get('production_companies') or []),
        'director_ids': '|'.join(str(person['id']) for person in credits.get('crew') or [] if person.get('job') == 'Director'),
        'cast_ids': '|'.join(str(person['id']) for person in cast[:10]),
        'collection_id': collection.get('id', ''),
        'revenue_worldwide_usd': int(payload.get('revenue') or 0),
        'collected_at': collected_at, 'features_as_of': '', 'pre_release_features_verified': 0,
        'source_url': f'https://www.themoviedb.org/movie/{movie_id}',
        'tmdb_status': payload.get('status') or '',
    }


def split_of(release_date):
    year = dt.date.fromisoformat(release_date).year
    return 'train' if year <= 2024 else 'val' if year == 2025 else 'test' if year == 2026 else 'outside_split'


def label_row(row, settle_days=90, min_budget=10_000):
    result = dict(row)
    observed = parse_date(row['collected_at'])
    release = parse_date(row['release_date'])
    budget, revenue = int(row['budget_usd']), int(row['revenue_worldwide_usd'])
    result.update(revenue_observed_at=row['collected_at'], success='', label_status='unlabeled',
                  eligible_for_training=0, completion_basis='no_verified_box_office_closure',
                  split=split_of(row['release_date']))
    if (observed-release).days < settle_days:
        result.update(outcome_complete='pending', exclusion_reason='recent_or_future_release')
    elif budget <= 0 or revenue <= 0:
        result.update(outcome_complete='unknown', exclusion_reason='missing_financial_values')
    elif budget < min_budget:
        result.update(outcome_complete='mature_unverified', exclusion_reason='budget_below_review_threshold')
    else:
        result.update(outcome_complete='mature_unverified', completion_basis=f'age_at_least_{settle_days}_days_heuristic',
                      label_status='provisional', success=int(revenue >= 2*budget),
                      eligible_for_training=1, exclusion_reason='')
    if row.get('tmdb_status') != 'Released':
        result.update(success='', label_status='unlabeled', eligible_for_training=0,
                      exclusion_reason='tmdb_status_not_released')
    return result


def sequel_proxy(row, collection):
    if not row.get('collection_id'):
        return '', 'not_in_collection_not_proof_of_no_sequel'
    if not collection:
        return '', 'collection_unavailable'
    parts = collection.get('parts') or []
    if not any(int(part['id']) == int(row['tmdb_id']) for part in parts):
        return '', 'movie_absent_from_collection_parts'
    earlier = [part for part in parts if parse_date(part.get('release_date'))
               and parse_date(part['release_date']) < parse_date(row['release_date'])
               and int(part['id']) != int(row['tmdb_id'])]
    if earlier:
        return 1, 'current_collection_chronology_proxy'
    if any(not parse_date(part.get('release_date')) for part in parts):
        return '', 'collection_has_undated_parts'
    return 0, 'current_collection_chronology_proxy'


def read_cache(path):
    entries, invalid = {}, 0
    if Path(path).exists():
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    entries[int(row['tmdb_id'])] = row
                except (ValueError, TypeError, KeyError):
                    invalid += 1
    return entries, invalid


def fetch_movie(client, movie_id):
    payload = client.get(f'/movie/{movie_id}', {'append_to_response': 'credits,release_dates'})
    if payload is None:
        return {'tmdb_id': movie_id, 'skip_reason': 'http_404', 'collected_at': utc_now()}
    return parse_movie(payload, utc_now())


def fetch_details(client, ids, cache_path, workers):
    """Mantiene un número acotado de tareas y conserva cada descarga completada."""
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    failures, completed = {}, 0
    if Path(cache_path).exists() and Path(cache_path).stat().st_size:
        with Path(cache_path).open('rb') as stream:
            stream.seek(-1, 2)
            needs_newline = stream.read(1) != b'\n'
        if needs_newline:
            with Path(cache_path).open('a', encoding='utf-8') as stream:
                stream.write('\n')
    remaining = iter(ids)
    executor = ThreadPoolExecutor(max_workers=workers)
    pending = {}
    try:
        with Path(cache_path).open('a', encoding='utf-8') as stream:
            def fill():
                while len(pending) < workers*2:
                    movie_id = next(remaining, None)
                    if movie_id is None:
                        break
                    pending[executor.submit(fetch_movie, client, movie_id)] = movie_id
            fill()
            while pending:
                ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in ready:
                    movie_id = pending.pop(future)
                    try:
                        row = future.result()
                    except DownloadError as error:
                        if 'HTTP 401' in str(error) or 'HTTP 403' in str(error):
                            client.stop.set()
                            raise
                        failures[str(movie_id)] = str(error)
                    else:
                        stream.write(json.dumps(row, ensure_ascii=False)+'\n')
                        stream.flush()
                    completed += 1
                    if completed % 100 == 0:
                        print(f'Detalles procesados: {completed}/{len(ids)}; errores: {len(failures)}', flush=True)
                fill()
    except BaseException:
        client.stop.set()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)
    return failures


def csv_output(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2010-01-01')
    parser.add_argument('--end', default=min(local_today(), dt.date(2026,12,31)).isoformat())
    parser.add_argument('--out', type=Path, default=Path('datos_actualizados/movies_2010_2026.csv'))
    parser.add_argument('--workdir', type=Path, default=Path('datos_actualizados/cache_tmdb_v2'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--rate', type=float, default=8.0)
    parser.add_argument('--settle-days', type=int, default=90)
    parser.add_argument('--min-budget', type=int, default=10_000,
                        help='Umbral de revisión para etiquetas; las filas se conservan en el CSV completo.')
    parser.add_argument('--interactive', action='store_true', help='Solicitar la credencial de forma oculta.')
    parser.add_argument('--check-auth', action='store_true', help='Validar acceso sin descargar películas.')
    parser.add_argument('--refresh', action='store_true', help='Volver a consultar todos los detalles cacheados.')
    parser.add_argument('--assemble-only', action='store_true', help='Ensamblar solo caché existente, sin red.')
    args = parser.parse_args(argv)
    start, end = parse_date(args.start), parse_date(args.end)
    if not start or not end or start > end or start < dt.date(2010,1,1) or end > dt.date(2026,12,31):
        parser.error('Usar fechas válidas y ordenadas entre 2010-01-01 y 2026-12-31.')
    if args.workers < 1 or args.rate <= 0 or args.settle_days < 1 or args.min_budget < 0:
        parser.error('workers/rate/settle-days deben ser positivos; min-budget no puede ser negativo.')
    client = None
    if not args.assemble_only:
        auth_type, secret = configured_credentials()
        if not secret and args.interactive:
            secret = getpass.getpass('Credencial de TMDb (entrada oculta; vacío cancela): ').strip()
            auth_type = 'token' if '.' in secret or secret.startswith('Bearer ') else 'key'
            secret = secret.removeprefix('Bearer ').strip()
        if not secret:
            raise DownloadError('No hay credencial. Obtenerla en https://www.themoviedb.org/settings/api y ejecutar con --interactive.')
        client = Client(auth_type, secret, rate=args.rate)
        if client.get('/configuration') is None:
            raise DownloadError('No se pudo validar la autenticación.')
        print('Autenticación de TMDb confirmada. La credencial no se guarda en archivos.', flush=True)
        if args.check_auth:
            return 0

    args.workdir.mkdir(parents=True, exist_ok=True)
    policy = {'version': 2, 'start': start.isoformat(), 'end': end.isoformat(),
              'discovery': 'TMDb discover release_date filters + theatrical types 2|3',
              'date_basis': 'earliest_worldwide_theatrical_release'}
    fingerprint = hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()[:12]
    ids_path = args.workdir/f'discovery_{fingerprint}.json'
    discovery = json.loads(ids_path.read_text(encoding='utf-8')) if ids_path.exists() else {'policy':policy,'months':{}}
    if discovery['policy'] != policy:
        raise DownloadError('La caché de descubrimiento no corresponde a estos filtros.')
    windows = list(month_windows(start,end))
    if client:
        for a,b in windows:
            key = f'{a.isoformat()}_{b.isoformat()}'
            if key not in discovery['months']:
                discovery['months'][key] = sorted(discover_window(client,a,b))
                atomic_json(ids_path,discovery)
            total = len({movie_id for group in discovery['months'].values() for movie_id in group})
            print(f'Descubrimiento {a:%Y-%m}: {total} IDs únicos acumulados',flush=True)
    elif not ids_path.exists():
        raise DownloadError('No existe una caché de descubrimiento para ese rango. No se creó un CSV vacío.')
    ids = {movie_id for group in discovery['months'].values() for movie_id in group}
    cache_path = args.workdir/'details.jsonl'
    cache, invalid_lines = read_cache(cache_path)
    today = local_today()
    def stale(movie_id):
        old = cache.get(movie_id)
        if not old or args.refresh:
            return True
        release, collected = parse_date(old.get('release_date')), parse_date(old.get('collected_at'))
        return collected != today and (release is None or (today-release).days <= 365)
    todo = sorted(movie_id for movie_id in ids if stale(movie_id))
    print(f'IDs descubiertos: {len(ids)}; detalles pendientes/por actualizar: {len(todo)}',flush=True)
    failures = fetch_details(client,todo,cache_path,args.workers) if client and todo else {}
    cache, invalid_lines = read_cache(cache_path)
    unresolved = ids-set(cache)

    collection_path = args.workdir/'collections.json'
    collections = json.loads(collection_path.read_text(encoding='utf-8')) if collection_path.exists() else {}
    selected = []
    for movie_id in sorted(ids):
        row = cache.get(movie_id)
        if not row or row.get('skip_reason'):
            continue
        release = parse_date(row['release_date'])
        if start <= release <= end:
            selected.append(row)
    for cid in sorted({str(row['collection_id']) for row in selected if row.get('collection_id')}):
        if client and (cid not in collections or args.refresh):
            try:
                collections[cid] = client.get(f'/collection/{cid}')
            except DownloadError:
                continue  # La película se conserva y la inferencia de secuela queda desconocida.
            atomic_json(collection_path,collections)

    rows = []
    for row in sorted(selected,key=lambda value:(value['release_date'],value['tmdb_id'])):
        labeled = label_row(row,args.settle_days,args.min_budget)
        labeled['is_sequel'], labeled['sequel_inference'] = sequel_proxy(row,collections.get(str(row['collection_id'])))
        rows.append({column:labeled.get(column,'') for column in COLUMNS})
    complete_download = len(discovery['months'])==len(windows) and not unresolved and not failures
    output = args.out if complete_download else args.out.with_name(args.out.stem+'.partial'+args.out.suffix)
    csv_output(output,rows)
    eligible = [row for row in rows if row['eligible_for_training']]
    training_path = output.with_name(output.stem+'_eligible'+output.suffix)
    csv_output(training_path,eligible)
    counts = Counter((row['split'],row['outcome_complete']) for row in rows)
    summary = {'data_origin':'TMDb API live responses or their dated cache; no simulated records',
        'policy':policy,'run_at':utc_now(),'download_complete':complete_download,
        'discovered_ids':len(ids),'rows':len(rows),'eligible_provisional':len(eligible),
        'unresolved_ids':sorted(unresolved),'failed_requests':failures,'invalid_cache_lines':invalid_lines,
        'counts':[{'split':split,'outcome_complete':status,'count':count} for (split,status),count in sorted(counts.items())],
        'eligible_by_year':dict(sorted(Counter(row['release_date'][:4] for row in eligible).items())),
        'eligible_by_split_class':dict(sorted(Counter(f'{row["split"]}:class_{row["success"]}' for row in eligible).items())),
        'settle_days':args.settle_days,'minimum_budget_for_labels':args.min_budget,
        'output':str(output.resolve()),'eligible_output':str(training_path.resolve()),
        'limitations':['Age threshold does not verify final box-office closure; labels are provisional.',
            'Revenue observation date is the extraction timestamp, not a published financial reporting date.',
            'Current metadata and collection membership are not certified historical pre-release snapshots.',
            'Collection chronology is only a sequel proxy; unknowns remain blank.',
            'Coverage depends on TMDb discovery and reported financial values.']}
    atomic_json(output.with_suffix('.summary.json'),summary)
    print(f'CSV de datos reales: {output.resolve()} ({len(rows)} filas)',flush=True)
    print(f'CSV elegible con etiquetas PROVISIONALES: {training_path.resolve()} ({len(eligible)} filas)',flush=True)
    for (split,status),count in sorted(counts.items()):
        print(f'  {split:8s} {status:20s} {count}')
    print('No se certifica recaudación final por el solo transcurso de 90 días.')
    if not complete_download:
        print('Descarga parcial: volver a ejecutar el mismo comando para reanudar.')
        return 1
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except DownloadError as error:
        print(f'ERROR: {error}',file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print('\nInterrumpido. Se conserva la caché; repetir el comando reanuda la descarga.',file=sys.stderr)
        raise SystemExit(130)
