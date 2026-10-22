#!/usr/bin/env python3
"""Read-only final publication scan; run --scan only after snapshot refresh.

The source snapshot and SQLite database are never edited. All JSON, JSONL,
CSV cells and every SQLite TEXT value are inspected, including JSON encoded
inside strings. Findings contain locations and categories, never matched
secret values or internal thinking text. --self-test uses temporary fixtures.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
from urllib.parse import unquote


csv.field_size_limit(sys.maxsize)
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT=ROOT/'.runtime/github-publish/frontend/public/snapshot'
DEFAULT_DATABASE=ROOT/'data/deployment-snapshot.sqlite3'
DEFAULT_REPORT=ROOT/'reports/repair_2026-10-20/public_scan_final.json'
MAX_DEPTH=128
SOURCE_CHECKER=ROOT/'scripts/sync-public-evidence.py'
UNSET=object()
REQUEST_REASONING_LEVELS={'low','medium','high'}


def load_public_checker():
    spec=importlib.util.spec_from_file_location('ttf_final_public_evidence_checker',SOURCE_CHECKER)
    if spec is None or spec.loader is None:
        raise RuntimeError('Public evidence checker is unavailable')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER=load_public_checker()
BLOCKED_KEY=CHECKER.BLOCKED_KEY
LOCAL_PATH=re.compile(r'(?<![A-Za-z0-9:/])(?:'+CHECKER.LOCAL_PATH.pattern+r'|(?:file://(?:localhost)?/[^\s"\'<>]+)|'
                      r'(?:[A-Za-z]:[\\/](?:Users|Documents and Settings|Windows|Program Files)[\\/][^\r\n"\'<>]+)|'
                      r'(?:/(?:Applications|Library|Volumes|usr|opt|etc)/[^\s"\'<>]+))',re.I)
URL_CREDENTIALS=re.compile(r'https?://[^\s/@:]+:[^\s/@]+@',re.I)
EXTRA_CREDENTIAL=re.compile(r'\b(?:AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,}|'
                            r'xox[baprs]-[A-Za-z0-9-]{16,}|'
                            r'eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})\b')
REDACTED=re.compile(r'^\s*["\']?(?:\[credential removed\]|\[private key removed\]|\[redacted\]|<redacted>|redacted|\*+)(?=$|[\s,"\';])',re.I)


def digest_file(path: Path) -> str:
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()


def relative(path: Path) -> str:
    try:return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:return path.name


def safe_label(text: str) -> str:
    """Location keys can themselves contain private values; never echo them."""
    if LOCAL_PATH.search(text):return '[local-path-key]'
    if (CHECKER.CREDENTIAL_VALUE.search(text) or CHECKER.BEARER.search(text)
            or EXTRA_CREDENTIAL.search(text) or CHECKER.PRIVATE_KEY_MARKER.search(text)
            or URL_CREDENTIALS.search(text)):
        return '[private-value-key]'
    return text[:240]


def json_pointer(parent: str, key: str | int) -> str:
    key=safe_label(str(key)).replace('~','~0').replace('/','~1')
    return parent+'/'+key


def reject_constant(value: str):
    raise ValueError('Nonfinite JSON is not accepted')


class PublicScanner:
    def __init__(self):
        self.findings=[]
        self._seen_findings=set()
        self.counts=Counter()

    def finding(self,category: str,location: dict):
        row={'category':category,**location}
        serial=json.dumps(row,ensure_ascii=False,sort_keys=True)
        if serial not in self._seen_findings:
            self._seen_findings.add(serial)
            self.findings.append(row)

    def inspect_key(self,key: str,location: dict,value=UNSET,defer_config_value: bool=False):
        self.counts['object_keys']+=1
        # The exact Ollama ``think`` enum is request metadata. Its spelling or
        # arbitrary prose values are not an exception to private-text checks.
        config=(key=='think' and isinstance(value,str) and value in REQUEST_REASONING_LEVELS)
        deferred=(key=='think' and defer_config_value)
        if BLOCKED_KEY.search(key) and not config and not deferred:
            self.finding('blocked_credential_or_thinking_field',location)
        self.inspect_text(key,{**location,'part':'field_name'},decode=False)

    def inspect_text(self,value: str,location: dict,depth: int=0,decode: bool=True):
        self.counts['text_values']+=1
        if LOCAL_PATH.search(value):self.finding('local_filesystem_path',location)
        if CHECKER.CREDENTIAL_VALUE.search(value) or EXTRA_CREDENTIAL.search(value):
            self.finding('private_credential_value',location)
        if CHECKER.BEARER.search(value):self.finding('bearer_credential',location)
        if CHECKER.PRIVATE_KEY_MARKER.search(value):self.finding('private_key_material',location)
        if URL_CREDENTIALS.search(value):self.finding('url_embedded_credentials',location)
        for match in CHECKER.ASSIGNMENT.finditer(value):
            assigned=value[match.start()+len(match.group(1)):]
            if not REDACTED.match(assigned):
                self.finding('credential_assignment_in_text',location)
        # Inspect complete JSON structurally so a legitimate request enum can
        # be distinguished from a thinking body. Partial/malformed JSON keeps
        # the conservative text check, even if its visible value looks valid.
        decoded=UNSET
        stripped=value.strip()
        if decode and stripped.startswith(('{','[','"')):
            try:decoded=json.loads(stripped,parse_constant=reject_constant)
            except (ValueError,TypeError,RecursionError):pass
        if decoded is UNSET and CHECKER.EMBEDDED_BLOCKED_KEY.search(value):
            self.finding('embedded_credential_or_thinking_field',location)
        if not decode:return
        if decoded is not UNSET:
            if depth>=MAX_DEPTH:
                self.finding('nested_value_depth_exceeded',location)
            elif not isinstance(decoded,str) or decoded!=value:
                self.counts['decoded_json_strings']+=1
                self.inspect_value(decoded,{**location,'pointer':location.get('pointer','')+'/$decoded'},depth+1)
        if '%' in value:
            decoded_url=unquote(value)
            if decoded_url!=value:
                if depth>=MAX_DEPTH:self.finding('nested_value_depth_exceeded',location)
                else:self.inspect_text(decoded_url,{**location,'part':'url_decoded_text'},depth+1,decode=False)

    def inspect_value(self,value,location: dict,depth: int=0):
        self.counts['structured_values']+=1
        if depth>MAX_DEPTH:
            self.finding('nested_value_depth_exceeded',location)
            return
        if isinstance(value,dict):
            for key,row in value.items():
                child={**location,'pointer':json_pointer(location.get('pointer',''),key)}
                self.inspect_key(str(key),child,row)
                self.inspect_value(row,child,depth+1)
        elif isinstance(value,list):
            for index,row in enumerate(value):
                self.inspect_value(row,{**location,'pointer':json_pointer(location.get('pointer',''),index)},depth+1)
        elif isinstance(value,str):self.inspect_text(value,location,depth)

    def scan_file(self,path: Path) -> dict:
        location={'artifact':relative(path),'format':path.suffix.lower().lstrip('.')}
        if path.is_symlink():
            self.finding('snapshot_symlink_not_scanned',location)
            return {**location,'status':'not_scanned'}
        before=path.stat()
        content=path.read_bytes()
        after=path.stat()
        row={**location,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
        if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
            self.finding('artifact_changed_during_read',location)
        try:text=content.decode('utf-8-sig')
        except UnicodeError:
            self.finding('invalid_utf8',location)
            return {**row,'status':'read_error'}
        extension=path.suffix.lower()
        if extension=='.json':
            try:value=json.loads(text,parse_constant=reject_constant)
            except (ValueError,RecursionError):
                self.finding('invalid_json',location)
                self.inspect_text(text,location)
            else:self.inspect_value(value,location)
        elif extension=='.jsonl':
            records=0
            for index,line in enumerate(text.split('\n'),1):
                if not line.strip():continue
                records+=1
                here={**location,'line':index}
                try:value=json.loads(line,parse_constant=reject_constant)
                except (ValueError,RecursionError):
                    self.finding('invalid_jsonl_record',here)
                    self.inspect_text(line,here)
                else:self.inspect_value(value,here)
            row['records']=records
        elif extension=='.csv':
            records=0
            try:
                reader=csv.reader(io.StringIO(text,newline=''),strict=True)
                headers=next(reader,[])
                for index,header in enumerate(headers):
                    self.inspect_key(header,{**location,'header_index':index+1,'column':safe_label(header)},defer_config_value=True)
                for index,values in enumerate(reader,2):
                    if not values:continue
                    records+=1
                    if len(values)!=len(headers):
                        self.finding('csv_column_count_mismatch',{**location,'row':index})
                    for column,value in enumerate(values):
                        header=headers[column] if column<len(headers) else '[extra-column]'
                        here={**location,'row':index,'physical_line_end':reader.line_num,
                              'column_index':column+1,'column':safe_label(header)}
                        if header=='think':self.inspect_key(header,here,value)
                        self.inspect_text(value,here)
            except csv.Error:
                self.finding('invalid_csv',location)
            row['records']=records
        else:self.finding('unsupported_snapshot_file',location)
        row['status']='scanned'
        return row

    def scan_snapshot(self,directory: Path) -> dict:
        if not directory.is_dir() or directory.is_symlink():
            self.finding('snapshot_directory_missing_or_symlink',{'artifact':relative(directory),'format':'directory'})
            return {'artifact':relative(directory),'files':[],'file_count':0,'bytes':0}
        files=[]
        for path in sorted(directory.rglob('*')):
            if path.is_file() or path.is_symlink():
                try:files.append(self.scan_file(path))
                except OSError:self.finding('snapshot_read_error',{'artifact':relative(path),'format':path.suffix.lstrip('.')})
        return {'artifact':relative(directory),'file_count':len(files),
                'bytes':sum(row.get('bytes',0) for row in files),'files':files}

    def scan_database(self,path: Path) -> dict:
        location={'artifact':relative(path),'format':'sqlite'}
        report={**location,'access':'mode=ro; PRAGMA query_only=ON; read transaction',
                'tables':[],'table_count':0,'rows':0,'text_cells':0,'modified':False}
        if not path.is_file() or path.is_symlink():
            self.finding('database_missing_or_symlink',location)
            report['status']='not_scanned'
            return report
        before=digest_file(path)
        report.update(bytes=path.stat().st_size,sha256_before=before)
        connection=None
        try:
            connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            tables=[('sqlite_schema',)]+connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            for (name,) in tables:
                table={'table':safe_label(name),'rows':0,'text_cells':0,'text_cells_by_column':{}}
                report['tables'].append(table)
                self.inspect_key(name,{**location,'table':safe_label(name),'part':'table_name'})
                quoted='"'+name.replace('"','""')+'"'
                try:
                    cursor=connection.execute('SELECT * FROM '+quoted)
                    columns=[entry[0] for entry in cursor.description or []]
                    for column in columns:
                        self.inspect_key(column,{**location,'table':safe_label(name),'column':safe_label(column),'part':'column_name'},defer_config_value=True)
                    for index,values in enumerate(cursor,1):
                        table['rows']+=1
                        for column,value in zip(columns,values):
                            if column=='think':
                                self.inspect_key(column,{**location,'table':safe_label(name),'row_number':index,'column':safe_label(column)},value)
                            if isinstance(value,str):
                                table['text_cells']+=1
                                label=safe_label(column)
                                table['text_cells_by_column'][label]=table['text_cells_by_column'].get(label,0)+1
                                self.inspect_text(value,{**location,'table':safe_label(name),'row_number':index,'column':label})
                except (sqlite3.Error,UnicodeError):
                    self.finding('database_table_read_error',{**location,'table':safe_label(name)})
                    table['status']='read_error'
                else:table['status']='scanned'
            report.update(table_count=len(report['tables']),rows=sum(row['rows'] for row in report['tables']),
                          text_cells=sum(row['text_cells'] for row in report['tables']),status='scanned')
        except sqlite3.Error:
            self.finding('database_read_error',location)
            report['status']='read_error'
        finally:
            if connection is not None:connection.close()
        after=digest_file(path)
        report.update(sha256_after=after,unchanged=before==after)
        if before!=after:self.finding('database_changed_during_scan',location)
        return report


def build_report(snapshot: Path,database: Path) -> dict:
    scanner=PublicScanner()
    snapshot_report=scanner.scan_snapshot(snapshot)
    database_report=scanner.scan_database(database)
    categories=dict(Counter(row['category'] for row in scanner.findings))
    path_fields=Counter((row.get('artifact'),row.get('table'),row.get('column'))
                       for row in scanner.findings if row['category']=='local_filesystem_path'
                       and row.get('format') in {'csv','sqlite'})
    return {'generated_at':datetime.now(timezone.utc).isoformat(),
            'status':'passed' if not scanner.findings else 'needs_attention',
            'scan_kind':'complete_refreshed_public_snapshot_and_sqlite_text',
            'scanner_sha256':digest_file(Path(__file__)),
            'public_checker_sha256':digest_file(SOURCE_CHECKER),
            'no_model_calls':True,'no_network_calls':True,'source_records_modified':False,
            'thinking_char_count_allowed':True,'exact_think_request_enum_allowed':sorted(REQUEST_REASONING_LEVELS),
            'sensitive_matched_values_included':False,
            'scope':'All snapshot files; JSON/JSONL including recursively encoded JSON strings; every CSV cell; every SQLite table and every TEXT value regardless of declared column type.',
            'snapshot':snapshot_report,'database':database_report,'inspection_counts':dict(scanner.counts),
            'finding_count':len(scanner.findings),'findings_by_category':categories,
            'csv_sqlite_path_fields':[{'artifact':artifact,**({'table':table} if table else {}),
                                       'column':column,'finding_count':count}
                                      for (artifact,table,column),count in sorted(path_fields.items(),key=lambda row:str(row[0]))],
            'findings':scanner.findings,
            'verification_boundary':'Pattern-based credential/private-path and explicit thinking-field checks; source news facts and forecasts are not altered or evaluated by this scan.'}


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.directory=Path(self.temporary.name)
        self.scanner=PublicScanner()
    def tearDown(self):self.temporary.cleanup()
    def file(self,name,text):
        path=self.directory/name;path.write_text(text,encoding='utf-8');return path
    def categories(self):return {row['category'] for row in self.scanner.findings}
    def test_clean_counts_and_reasoning_configuration_allowed(self):
        self.scanner.inspect_value({'thinking_char_count':1024,'reasoning':'medium','summary':'未来供给预期'}, {})
        self.assertEqual(self.scanner.findings,[])
    def test_uses_current_source_checker(self):
        self.assertEqual(SOURCE_CHECKER,ROOT/'scripts/sync-public-evidence.py')
        self.assertEqual(Path(CHECKER.__file__).resolve(),SOURCE_CHECKER.resolve())
    def test_original_model_metadata_request_enums_are_not_private_thinking(self):
        value={'result':{'_meta':{'reasoning':'medium','model_calls':[
            {'attempt':1,'think':'medium','thinking_char_count':5791,'raw_content':json.dumps({'summary':'来源约束风险仍在'})},
            {'attempt':2,'think':'high','semantic_independent_regeneration':True,'thinking_char_count':19200}],
            'request':json.dumps({'options':{'think':'low'}})}}}
        original=json.dumps(value,ensure_ascii=False,sort_keys=True)
        self.scanner.inspect_value(value,{})
        self.assertEqual(self.scanner.findings,[])
        self.assertEqual(json.dumps(value,ensure_ascii=False,sort_keys=True),original)
    def test_non_enum_think_and_all_thinking_bodies_stay_blocked(self):
        for key,value in [('think','private internal body'),('think','HIGH'),('think',' medium'),
                          ('think','medium '),('think',True),('think',None),('think',['high']),
                          ('Think','high'),('thinking','medium'),('raw_think','high')]:
            with self.subTest(key=key,value=value):
                scanner=PublicScanner();scanner.inspect_value({key:value},{})
                self.assertTrue(any(row['category']=='blocked_credential_or_thinking_field' for row in scanner.findings))
    def test_nested_encoded_request_enum_is_allowed_but_partial_json_is_blocked(self):
        self.scanner.inspect_value({'payload':json.dumps(json.dumps({'model_calls':[{'think':'medium'}]}))},{})
        self.assertEqual(self.scanner.findings,[])
        for partial in ('{"think":"medium"','partial {"think":"high"}',
                        '{"think":"private"','{"thinking":"high"'):
            with self.subTest(partial=partial):
                scanner=PublicScanner();scanner.inspect_text(partial,{})
                self.assertTrue(any(row['category']=='embedded_credential_or_thinking_field' for row in scanner.findings))
    def test_jsonl_original_metadata_enums_and_non_enum_blocking(self):
        self.scanner.scan_file(self.file('metadata.jsonl',json.dumps({'meta':{'think':'medium'}})+'\n'+
                                         json.dumps({'meta':json.dumps({'think':'high'})})+'\n'))
        self.assertEqual(self.scanner.findings,[])
        self.scanner.scan_file(self.file('body.jsonl',json.dumps({'meta':{'think':'private body'}})+'\n'))
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
    def test_csv_original_request_enum_cells_and_metadata_are_allowed(self):
        stream=io.StringIO(newline='');writer=csv.writer(stream)
        writer.writerow(['think','model_metadata']);writer.writerow(['medium',json.dumps({'model_calls':[{'think':'high'}]})])
        self.scanner.scan_file(self.file('request.csv',stream.getvalue()))
        self.assertEqual(self.scanner.findings,[])
        self.scanner.scan_file(self.file('body.csv','think\nprivate internal body\n'))
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
    def test_sqlite_original_request_enums_and_nested_metadata_unchanged(self):
        path=self.directory/'metadata.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE calls(think TEXT,metadata TEXT)')
            connection.execute('INSERT INTO calls VALUES(?,?)',('medium',json.dumps({'_meta':{'model_calls':[{'think':'high'}]}})))
        before=path.read_bytes();report=self.scanner.scan_database(path)
        self.assertTrue(report['unchanged'])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.scanner.findings,[])
        with sqlite3.connect(path) as connection:connection.execute('INSERT INTO calls VALUES(?,?)',('private internal body','{}'))
        self.scanner.scan_database(path)
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
    def test_nested_encoded_json_thinking_detected_without_echo(self):
        secret='private internal analysis body'
        encoded=json.dumps(json.dumps({'thinking':secret}))
        self.scanner.inspect_value({'raw_content':encoded},{'artifact':'fixture.json'})
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
        self.assertNotIn(secret,json.dumps(self.scanner.findings))
        self.assertGreaterEqual(self.scanner.counts['decoded_json_strings'],2)
    def test_jsonl_each_record_and_encoded_payload_scanned(self):
        path=self.file('fixture.jsonl',json.dumps({'ok':1})+'\n'+json.dumps({'payload':json.dumps({'access_token':'not-public'})})+'\n')
        self.scanner.scan_file(path)
        self.assertTrue(any(row.get('line')==2 for row in self.scanner.findings))
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
    def test_jsonl_unicode_separators_are_string_content(self):
        content=json.dumps({'summary':'before\u2028middle\u2029after'},ensure_ascii=False)+'\n'+json.dumps({'ok':2})+'\n'
        result=self.scanner.scan_file(self.file('unicode.jsonl',content))
        self.assertEqual(result['records'],2)
        self.assertEqual(self.scanner.findings,[])
    def test_csv_multiline_and_duplicate_headers_scan_every_cell(self):
        stream=io.StringIO(newline='');writer=csv.writer(stream)
        writer.writerow(['id','payload','payload'])
        writer.writerow(['1','safe\nsecond line','/Users/example/private/input.csv'])
        self.scanner.scan_file(self.file('fixture.csv',stream.getvalue()))
        match=next(row for row in self.scanner.findings if row['category']=='local_filesystem_path')
        self.assertEqual(match['column_index'],3)
        self.assertEqual(match['row'],2)
        self.assertEqual(match['physical_line_end'],3)
        self.assertNotIn('/Users/',json.dumps(self.scanner.findings))
    def test_csv_json_field_decodes_thinking(self):
        stream=io.StringIO(newline='');writer=csv.writer(stream)
        writer.writerow(['detail_result']);writer.writerow([json.dumps({'raw_thinking':'hidden'})])
        self.scanner.scan_file(self.file('fixture.csv',stream.getvalue()))
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
    def test_large_csv_field_is_not_skipped_by_default_csv_limit(self):
        stream=io.StringIO(newline='');writer=csv.writer(stream)
        writer.writerow(['input_snapshot']);writer.writerow(['x'*150000+' /Users/example/private/input'])
        self.scanner.scan_file(self.file('large.csv',stream.getvalue()))
        self.assertNotIn('invalid_csv',self.categories())
        self.assertIn('local_filesystem_path',self.categories())
    def test_sqlite_all_tables_all_actual_text_types_and_unchanged(self):
        path=self.directory/'fixture.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.executescript('CREATE TABLE one(id INTEGER PRIMARY KEY,payload TEXT); CREATE TABLE "two\""quoted"(declared_integer INTEGER,detail TEXT);')
            connection.execute('INSERT INTO one(payload) VALUES(?)',(json.dumps({'payload':json.dumps({'thinking':'private'})}),))
            connection.execute('INSERT INTO "two\""quoted" VALUES(?,?)',('/home/example/private/data','safe'))
        before=path.read_bytes();report=self.scanner.scan_database(path)
        self.assertEqual(report['table_count'],3)
        self.assertEqual(next(row['text_cells'] for row in report['tables'] if row['table']=='one'),1)
        self.assertEqual(next(row['text_cells'] for row in report['tables'] if row['table']=='two"quoted'),2)
        self.assertTrue(report['unchanged'])
        self.assertEqual(path.read_bytes(),before)
        self.assertTrue(any(row.get('column')=='declared_integer' and row['category']=='local_filesystem_path' for row in self.scanner.findings))
        self.assertIn('blocked_credential_or_thinking_field',self.categories())
    def test_credential_values_and_assignments_never_echoed(self):
        sample='ghp_'+'X'*30
        self.scanner.inspect_text(sample+' password=hidden-value',{'column':'payload'})
        self.assertIn('private_credential_value',self.categories())
        self.assertIn('credential_assignment_in_text',self.categories())
        self.assertNotIn(sample,json.dumps(self.scanner.findings))
        self.assertNotIn('hidden-value',json.dumps(self.scanner.findings))
    def test_partial_private_key_and_url_credentials_detected(self):
        self.scanner.inspect_text('-----BEGIN OPENSSH PRIVATE KEY-----\nhttps://name:password@example.com',{})
        self.assertIn('private_key_material',self.categories())
        self.assertIn('url_embedded_credentials',self.categories())
    def test_invalid_json_is_blocking_and_still_checks_text(self):
        self.scanner.scan_file(self.file('fixture.json','partial {"thinking":"private"'))
        self.assertIn('invalid_json',self.categories())
        self.assertIn('embedded_credential_or_thinking_field',self.categories())
    def test_invalid_csv_and_extra_columns_fail_closed(self):
        self.scanner.scan_file(self.file('bad.csv','id,payload\n1,"unterminated'))
        self.scanner.scan_file(self.file('extra.csv','id\n1,extra'))
        self.assertIn('invalid_csv',self.categories())
        self.assertIn('csv_column_count_mismatch',self.categories())
    def test_path_keys_are_redacted_in_locations(self):
        self.scanner.inspect_value({'/Users/example/private.json':'hash'},{})
        self.assertIn('local_filesystem_path',self.categories())
        self.assertNotIn('/Users/',json.dumps(self.scanner.findings))
    def test_windows_file_uri_and_url_encoded_paths(self):
        for value in [r'C:\Users\example\private.csv','file:///etc/private.conf','%2FUsers%2Fexample%2Fprivate.json']:
            self.scanner.inspect_text(value,{})
        self.assertIn('local_filesystem_path',self.categories())
    def test_symlink_snapshot_not_followed(self):
        secret=self.file('private.json','{"thinking":"hidden"}')
        directory=self.directory/'snapshot';directory.mkdir();(directory/'link.json').symlink_to(secret)
        self.scanner.scan_snapshot(directory)
        self.assertIn('snapshot_symlink_not_scanned',self.categories())
        self.assertNotIn('blocked_credential_or_thinking_field',self.categories())
    def test_nonfinite_json_is_invalid(self):
        self.scanner.scan_file(self.file('fixture.json','{"number":NaN}'))
        self.assertIn('invalid_json',self.categories())
    def test_plain_redacted_assignments_are_not_credentials(self):
        self.scanner.inspect_text('api_key=[credential removed]',{})
        self.assertNotIn('credential_assignment_in_text',self.categories())
    def test_public_url_home_path_is_not_a_local_filesystem_path(self):
        for url in ['https://example.com/home/article','https://example.com/Users/public-profile']:
            self.scanner.inspect_text(url,{})
        self.assertNotIn('local_filesystem_path',self.categories())
    def test_csv_sqlite_path_fields_report_location_without_value(self):
        snapshot=self.directory/'snapshot';snapshot.mkdir()
        (snapshot/'sample.csv').write_text('file\n/Users/example/private.txt\n')
        database=self.directory/'fixture.sqlite3'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE settings(payload TEXT)')
            connection.execute('INSERT INTO settings VALUES(?)',('/home/example/local/data',))
        report=build_report(snapshot,database)
        self.assertEqual(report['status'],'needs_attention')
        fields=report['csv_sqlite_path_fields']
        self.assertTrue(any(row.get('column')=='file' for row in fields))
        self.assertTrue(any(row.get('table')=='settings' and row.get('column')=='payload' for row in fields))
        self.assertNotIn('/Users/',json.dumps(report))
        self.assertNotIn('/home/',json.dumps(report))


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--self-test',action='store_true')
    action.add_argument('--scan',action='store_true',help='Run only after the final publication snapshot has been refreshed')
    parser.add_argument('--snapshot',type=Path,default=DEFAULT_SNAPSHOT)
    parser.add_argument('--database',type=Path,default=DEFAULT_DATABASE)
    parser.add_argument('--report',type=Path,default=DEFAULT_REPORT)
    args=parser.parse_args()
    if args.self_test:
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(ScanTests)
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    report=build_report(args.snapshot,args.database)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.report.with_suffix(args.report.suffix+'.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(args.report)
    print(json.dumps({'status':report['status'],'snapshot_files':report['snapshot']['file_count'],
                      'sqlite_tables':report['database']['table_count'],'sqlite_text_cells':report['database']['text_cells'],
                      'findings':report['finding_count'],'categories':report['findings_by_category'],
                      'csv_sqlite_path_fields':report['csv_sqlite_path_fields'],'report':relative(args.report)},ensure_ascii=False))
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
