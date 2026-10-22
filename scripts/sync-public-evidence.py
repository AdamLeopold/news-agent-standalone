#!/usr/bin/env python3
"""Copy an explicit source/evidence allowlist into the existing publish checkout.

No Git commands, database access, network requests, model calls or service
actions. Run after all comparison records and final reports are complete.
Original evidence files remain unchanged; only public report copies are redacted.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET


ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'.runtime/github-publish'
EVIDENCE=Path('reports/repair_2026-10-20')
VERIFIED_UI_IMAGES=(EVIDENCE/'error_ui_final.png',EVIDENCE/'error_ui_v2.png',
                    EVIDENCE/'error_ui_v2_2.jpg',EVIDENCE/'error_ui_v2_3.jpg')
PUBLICATION_MANIFEST=Path('reports/publication_manifest.json')
PUBLICATION_SCHEMA='ttf-publication-manifest-v1'
FINAL_REPORTS=tuple(Path(f'reports/logic_repair_validation_2026-10-20.{suffix}') for suffix in ('json','md'))
PROVENANCE_EVIDENCE=tuple(EVIDENCE/'source_provenance_output_feasibility_v2_3'/name for name in (
    'actual/ninth_current/attempt-1-request.json', 'actual/ninth_current/attempt-1-response.json',
    'actual/ninth_current/attempt-2-request.json', 'actual/ninth_current/attempt-2-response.json',
    'actual/ninth_current/result.json',
    'actual/ninth_manual/attempt-1-request.json', 'actual/ninth_manual/attempt-1-response.json',
    'actual/ninth_manual/attempt-2-request.json', 'actual/ninth_manual/attempt-2-response.json',
    'actual/ninth_manual/result.json', 'actual/started.json', 'actual/terminal.json',
    'backend_source_provenance_examples.json',
    'baseline/production_ninth_current_live_actual_v2_3.json',
    'baseline/production_ninth_manual_brief_failed_actual_v2_3.json', 'build_proof.json',
    'independent_reviews/independent_source_provenance_pilot_final_source_review_v2_3.json',
    'independent_reviews/independent_source_provenance_pilot_ninth_current_actual_review_v2_3.json',
    'independent_reviews/independent_source_provenance_pilot_ninth_manual_actual_review_v2_3.json',
    'independent_reviews/independent_source_provenance_pilot_no_go_decision_v2_3.json',
    'ninth_current_candidate_request.json', 'ninth_manual_candidate_request.json',
    'origin_copy_manifest.json', 'proposal_manifest.json'))
SCRIPTS=('prepare-github.py','validate-news-logic.py','repair-validation-report.py',
         'validate-deployment.py','sync-public-evidence.py','apply-analysis-reviews.py','scan-public-snapshot.py')
LOGS=('analysis-tests.log','focused-tests.log','frontend-build.log','frontend-tests.log',
      'publish-frontend-build.log','publish-frontend-tests.log','publish-backend-tests.log',
      'publish-frontend-risk-final-tests.log','publish-frontend-risk-final-build.log',
      'publish-semantic-tests.log','public-scan-self-tests.log','report-self-tests.log','unit-tests.log',
      'final-unit-tests.log','final-release-unit-tests.log','source-feedback-report-self-tests.log',
      'risk-polarity-report-self-tests.log','risk-polarity-final-unit-tests.log',
      'risk-polarity-final-unit-tests-rerun.log','risk-polarity-focused-tests.log',
      'risk-polarity-focused-initial-tests.log','risk-conflict-citation-focused-tests.log',
      'sync-raw-preservation-self-tests.log','physical-unit-report-self-tests.log',
      'production-api-metadata-harness-self-tests-rerun.log',
      'production-api-metadata-harness-self-tests.log',
      'physical-unit-final-unit-tests.log',
      'physical-unit-final-unit-tests-rerun.log',
      'physical-unit-scoped-focused-tests.log',
      'physical-unit-scoped-focused-tests-rerun.log',
      'physical-unit-scoped-focused-tests-final.log',
      'publish-backend-physical-unit-tests.log',
      'evidence-slot-report-self-tests.log',
      'physical-unit-publication-identity-focused-tests.log',
      'evidence-slot-final-unit-tests.log',
      'evidence-slot-final-unit-tests-rerun.log',
      'evidence-hollow-physical-final-focused-tests.log',
      'evidence-slot-price-scope-focused-tests.log',
      'publish-backend-final-source-tests.log',
      'price-zero-final-unit-tests.log',
      'price-reaction-final-unit-tests.log',
      'notice-scope-final-unit-tests.log',
      'output-scope-final-unit-tests.log',
      'publish-backend-output-scope-final-tests.log',
      'output-contract-final-unit-tests.log',
      'output-contract-final-unit-tests-rerun.log',
      'output-contract-final-unit-tests-registered.log',
      'publish-backend-output-contract-final-tests.log',
      'source-binding-final-unit-tests.log',
      'source-binding-final-unit-tests-rerun.log',
      'publish-backend-source-binding-final-tests.log',
      'publish-backend-source-binding-final-tests-rerun.log',
      'operator-prose-final-unit-tests.log',
      'operator-prose-final-unit-tests-rerun.log',
      'publish-backend-operator-prose-final-tests.log',
      'publish-backend-notice-scope-final-tests.log',
      'publish-backend-notice-scope-final-tests-rerun.log',
      'frontend-notice-scope-final-tests.log',
      'frontend-notice-scope-final-build.log',
      'price-reaction-scope-focused-tests.log',
      'publish-backend-price-reaction-final-tests.log',
      'publish-backend-price-zero-final-tests.log',
      'price-zero-consistency-report-self-tests.log',
      'price-zero-observation-focused-tests.log',
      'price-zero-observed-role-focused-tests.log',
      'anchor-operational-report-self-tests.log')
FORBIDDEN_PARTS={'.runtime','.git','original_source','backups','backup','__pycache__','node_modules'}
BLOCKED_KEY=re.compile(r'password|passwd|secret|authorization|cookie|api.key|(?:^|_)token$|'
                       r'^(?:raw_)?(?:think|thinking|reasoning_content|chain_of_thought)(?:_content|_text)?$',re.I)
LOCAL_PATH=re.compile(r'(?:/Users/|/home/|/private/|/tmp/|/var/folders/)[^\s"\'<>]+')
CREDENTIAL_VALUE=re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|'
                            r'github_pat_[A-Za-z0-9_]{20,}|tskey-[A-Za-z0-9_-]{16,})\b')
BEARER=re.compile(r'\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*',re.I)
ASSIGNMENT=re.compile(r'(?i)(\b(?:password|passwd|client[_-]?secret|api[_-]?key|access[_-]?token|'
                      r'refresh[_-]?token|authorization|cookie)\b\s*[:=]\s*)'
                      r'(?:"[^"\n]*"|\'[^\'\n]*\'|[^\s,;&<>]+)')
PRIVATE_KEY=re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----',re.S)
PRIVATE_KEY_MARKER=re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')
EMBEDDED_BLOCKED_KEY=re.compile(r'["\'](?:password|passwd|[^"\']*secret|authorization|cookie|api.key|'
                                r'(?:raw_)?think(?:ing)?|reasoning_content|chain_of_thought)["\']\s*:',re.I)


def load_report_module():
    spec=importlib.util.spec_from_file_location('ttf_public_evidence_report',ROOT/'scripts/repair-validation-report.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('Could not load the report redaction helper')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPORT=load_report_module()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def redact_text(value: str) -> str:
    value=value.replace(str(ROOT)+'/', '')
    value=LOCAL_PATH.sub('[local path]',value)
    value=REPORT.safe(value)
    value=PRIVATE_KEY.sub('[private key removed]',value)
    value=re.sub(r'(https?://)[^\s/@:]+:[^\s/@]+@',r'\1[credentials removed]@',value)
    value=CREDENTIAL_VALUE.sub('[credential removed]',value)
    value=BEARER.sub('Bearer [credential removed]',value)
    return ASSIGNMENT.sub(lambda m:m.group(1)+'[credential removed]',value)


def public_safe(value):
    """Use report.safe recursively, including encoded JSON and path-like keys."""
    if isinstance(value,dict):
        # ``think`` is both an Ollama request configuration key and a possible
        # private prose field. Preserve only the three exact configuration
        # values, under an unambiguous public key; never republish prose.
        normalized=dict(value)
        reasoning=normalized.get('think')
        if isinstance(reasoning,str) and reasoning in ('low','medium','high'):
            if 'request_reasoning' in normalized and normalized['request_reasoning']!=reasoning:
                raise ValueError('Reasoning configuration keys conflict; review the source evidence')
            normalized['request_reasoning']=reasoning
            del normalized['think']
        retained=REPORT.safe({key:None for key in normalized})
        output={}
        for key,row in normalized.items():
            if key not in retained or BLOCKED_KEY.search(str(key)):
                continue
            clean_key=redact_text(str(key))
            if clean_key in output:
                raise ValueError('Redacted JSON keys collide; review the source evidence')
            output[clean_key]=public_safe(row)
        return REPORT.safe(output)
    if isinstance(value,list):
        return REPORT.safe([public_safe(row) for row in value])
    if isinstance(value,str):
        if value.lstrip().startswith(('{','[')):
            try:
                nested=json.loads(value)
            except (ValueError,TypeError):
                pass
            else:
                if isinstance(nested,(dict,list)):
                    clean=public_safe(nested)
                    # A saved model response is also a byte-identified audit
                    # artifact. Do not normalize its whitespace or Unicode
                    # escapes when recursive redaction changed nothing.
                    if clean==nested and redact_text(value)==value:
                        return value
                    return json.dumps(clean,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        return redact_text(value)
    return value


def check_public(value):
    """Fail closed on remaining report keys, local paths or credential markers."""
    keys=0
    if isinstance(value,dict):
        for key,row in value.items():
            keys+=1
            if BLOCKED_KEY.search(str(key)):
                raise ValueError('A blocked credential/thinking key remains in public evidence')
            check_public(str(key))
            keys+=check_public(row)
    elif isinstance(value,list):
        for row in value:
            keys+=check_public(row)
    elif isinstance(value,str):
        if LOCAL_PATH.search(value) or CREDENTIAL_VALUE.search(value) or BEARER.search(value) or PRIVATE_KEY_MARKER.search(value):
            raise ValueError('A local path or credential remains in public evidence')
        if EMBEDDED_BLOCKED_KEY.search(value):
            raise ValueError('An embedded credential/thinking key remains in public evidence')
        if value.lstrip().startswith(('{','[')):
            try:
                nested=json.loads(value)
            except (ValueError,TypeError):
                pass
            else:
                if isinstance(nested,(dict,list)):
                    keys+=check_public(nested)
    return keys


def reject_constant(value):
    raise ValueError('Non-finite JSON constant: '+value)


def read_stable(path: Path) -> bytes:
    before=path.stat()
    data=path.read_bytes()
    after=path.stat()
    if (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size):
        raise ValueError('Source changed while reading: '+path.name)
    return data


def sanitize_report(path: Path) -> tuple[bytes,int]:
    if path.suffix.lower() in ('.png','.jpg','.jpeg'):
        try:relative=path.relative_to(ROOT)
        except ValueError:raise ValueError('Image is outside the verified UI evidence allowlist')
        if relative not in VERIFIED_UI_IMAGES:
            raise ValueError('Image is outside the verified UI evidence allowlist')
        source_file(path)
        content=read_stable(path)
        png=content.startswith(b'\x89PNG\r\n\x1a\n') and content.endswith(b'IEND\xaeB`\x82')
        # Some captured UI files have fixed .png evidence names, but the
        # screenshot provider may return JPEG bytes. Keep the verified bytes
        # and original hashes rather than converting or renaming evidence.
        jpeg=content.startswith(b'\xff\xd8\xff') and content.endswith(b'\xff\xd9')
        if not (png or jpeg):
            raise ValueError('Verified UI evidence is not a complete PNG or JPEG image')
        return content,0
    source=read_stable(path).decode('utf-8')
    if path.suffix=='.json':
        # An in-progress/broken JSON file aborts all copying, never gets skipped.
        value=json.loads(source,parse_constant=reject_constant)
        clean=public_safe(value)
        checked=check_public(clean)
        return (json.dumps(clean,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode(),checked
    if path.suffix=='.xml':
        root=ET.fromstring(source)
        checked=0
        for node in root.iter():
            node.attrib=public_safe(node.attrib)
            for field in ('text','tail'):
                value=getattr(node,field)
                if value is not None:
                    setattr(node,field,public_safe(value))
            checked+=check_public(node.attrib)+check_public(node.text)+check_public(node.tail)
        clean=ET.tostring(root,encoding='utf-8',xml_declaration=True)+b'\n'
        return clean,checked
    clean=public_safe(source)
    return clean.encode('utf-8'),check_public(clean)


def allowed_relative(path: Path):
    if path.is_absolute() or any(part in FORBIDDEN_PARTS for part in path.parts):
        raise ValueError('Path is outside the publication allowlist')
    if path.name in {'original_source.tar','source_utf8_archive.json'} or any(part=='..' for part in path.parts):
        raise ValueError('Source archives and parent paths are never published')


def source_file(path: Path) -> Path:
    relative=path.relative_to(ROOT)
    allowed_relative(relative)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
        raise ValueError('Only regular files inside the source project may be copied')
    return relative


def selected_files() -> tuple[list[Path],list[Path]]:
    code=[]
    for directory in ('ttf_news','tests'):
        code.extend(source_file(path) for path in sorted((ROOT/directory).glob('*.py')))
    code.extend(source_file(path) for path in sorted((ROOT/'tests/fixtures').glob('*.json')))
    for directory in ('frontend/src','frontend/tests'):
        for path in sorted((ROOT/directory).rglob('*')):
            if path.is_file() and path.name!='.DS_Store' and path.suffix not in ('.pyc','.tsbuildinfo'):
                code.append(source_file(path))
    code.append(source_file(ROOT/'README.md'))
    code.append(source_file(ROOT/'deployment/README.md'))
    code.extend(source_file(ROOT/'scripts'/name) for name in SCRIPTS)
    reports=[source_file(ROOT/path) for path in FINAL_REPORTS]
    evidence=ROOT/EVIDENCE
    if not evidence.is_dir():
        raise ValueError('The repair evidence directory is missing')
    for pattern in ('*.json','*.xml'):
        reports.extend(source_file(path) for path in sorted(evidence.glob(pattern)))
    reports.append(source_file(evidence/'change_notes.md'))
    reports.extend(source_file(ROOT/path) for path in VERIFIED_UI_IMAGES)
    reports.extend(source_file(ROOT/path) for path in PROVENANCE_EVIDENCE)
    for name in LOGS:
        path=evidence/name
        if path.exists():
            reports.append(source_file(path))
    for stage in (*REPORT.STAGES,*REPORT.DETAIL_STAGES):
        if not re.fullmatch(r'[a-z0-9-]+',stage):
            raise ValueError('Unexpected comparison stage name')
        reports.extend(source_file(path) for path in sorted((evidence/stage).glob('*.json')))
    structured=ROOT/'reports/structured_v2_4'
    if structured.is_dir():
        for path in sorted(structured.rglob('*')):
            if path.is_file() and path.suffix in {'.json','.md','.xml','.log'}:
                reports.append(source_file(path))
    return sorted(set(code)),sorted(set(reports))


def destination(base: Path, relative: Path) -> Path:
    # The managed checkout itself lives in .runtime; copied relative paths may not.
    allowed_relative(relative)
    target=base/relative
    if base.is_symlink() or not base.resolve().is_relative_to(ROOT) or not target.resolve().is_relative_to(base.resolve()):
        raise ValueError('A destination resolves outside its publication directory')
    parent=target
    while parent!=base:
        if parent.is_symlink():
            raise ValueError('Symlink destinations are not accepted')
        parent=parent.parent
    return target


def atomic_write(path: Path,content: bytes,mode=0o644):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.sync-tmp')
    try:
        temporary.write_bytes(content)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def publication_entry(relative: Path, source: bytes, public: bytes, category: str) -> dict:
    """Describe both byte identities; a derived copy is never called original."""
    allowed_relative(relative)
    if relative==PUBLICATION_MANIFEST:
        raise ValueError('The publication manifest cannot include itself')
    if category not in ('source','evidence'):
        raise ValueError('Unknown publication artifact category')
    exact=source==public
    transformation=('exact_source_copy' if category=='source' else
                    'verified_ui_binary_copy' if relative in VERIFIED_UI_IMAGES else
                    'public_safe_'+{'.json':'json','.xml':'xml'}.get(relative.suffix,'text'))
    return {'source_bytes_sha256':digest(source),'public_bytes_sha256':digest(public),
            'transformation':transformation,'is_derived':not exact,
            'evidence_flags':{'source':category=='source','evidence':category=='evidence',
                              'original_bytes_preserved':exact,
                              'sanitization_applied':category=='evidence' and relative not in VERIFIED_UI_IMAGES}}


def build_publication_manifest(plans: list[tuple[Path,bytes,bytes,str]]) -> dict:
    artifacts={}
    for relative,source,public,category in plans:
        key=relative.as_posix()
        if key in artifacts:
            raise ValueError('Duplicate publication artifact path')
        artifacts[key]=publication_entry(relative,source,public,category)
    return {'schema_version':PUBLICATION_SCHEMA,'generated_at':datetime.now(timezone.utc).isoformat(),
            'scope':'Project-relative published source and evidence artifacts; source and public byte identities are distinct.',
            'originals_modified':False,'manifest_self_excluded':True,'artifacts':artifacts}


def validate_publication_manifest(manifest: dict, plans: list[tuple[Path,bytes,bytes,str]]) -> None:
    """Validate the full planned graph before any public files are written."""
    if manifest.get('schema_version')!=PUBLICATION_SCHEMA or manifest.get('manifest_self_excluded') is not True or manifest.get('originals_modified') is not False:
        raise ValueError('Invalid publication manifest header')
    expected={}
    for relative,source,public,category in plans:
        key=relative.as_posix()
        if key in expected:
            raise ValueError('Duplicate publication artifact path')
        expected[key]=publication_entry(relative,source,public,category)
    entries=manifest.get('artifacts')
    if not isinstance(entries,dict) or set(entries)!=set(expected):
        raise ValueError('Missing or unexpected publication artifact mapping')
    for key,entry in entries.items():
        # The equality also checks transform/evidence flags, so a stale copy
        # cannot be relabelled as byte-identical evidence.
        if entry!=expected[key]:
            raise ValueError('Stale or changed publication artifact mapping')


def sync(dry_run=False):
    if not (TARGET/'.git').exists():
        raise ValueError('Expected the existing managed Git publish checkout')
    code,reports=selected_files()
    prepared=[]
    artifact_plans=[]
    checked_keys=0
    # Validate every source and report before writing anything. No partial JSON
    # or failed redaction can leave a half-refreshed evidence publication.
    for relative in code:
        source=ROOT/relative
        content=read_stable(source)
        prepared.append((destination(TARGET,relative),content,source.stat().st_mode&0o777))
        artifact_plans.append((relative,content,content,'source'))
    report_hashes={}
    for relative in reports:
        original=read_stable(ROOT/relative)
        clean,keys=sanitize_report(ROOT/relative)
        if read_stable(ROOT/relative)!=original:
            raise ValueError('Source changed while deriving publication identity: '+relative.name)
        checked_keys+=keys
        report_hashes[str(relative)]=digest(clean)
        artifact_plans.append((relative,original,clean,'evidence'))
        web_relative=relative.relative_to('reports')
        for base,name in ((TARGET,relative),(TARGET/'frontend/public/reports',web_relative),
                          (ROOT/'frontend/public/reports',web_relative)):
            prepared.append((destination(base,name),clean,0o644))
    manifest=build_publication_manifest(artifact_plans)
    validate_publication_manifest(manifest,artifact_plans)
    check_public(manifest)
    manifest_bytes=(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    for base,name in ((TARGET,PUBLICATION_MANIFEST),
                      (TARGET/'frontend/public/reports',Path('publication_manifest.json')),
                      (ROOT/'frontend/public/reports',Path('publication_manifest.json'))):
        prepared.append((destination(base,name),manifest_bytes,0o644))
    if not dry_run:
        for path,content,mode in prepared:
            atomic_write(path,content,mode)
            if digest(path.read_bytes())!=digest(content):
                raise ValueError('Destination verification failed for '+path.name)
    return {'status':'validated_dry_run' if dry_run else 'copied',
            'source_files':len(code),'sanitized_evidence_files':len(reports),
            'website_evidence_copies':len(reports)*2,'total_file_writes':len(prepared),
            'report_json_keys_checked':checked_keys,'blocked_keys_remaining':0,
            'local_paths_or_credentials_remaining':0,'originals_modified':False,
            'git_service_database_actions':False,'sanitized_report_sha256':report_hashes,
            'publication_manifest':{'file':PUBLICATION_MANIFEST.as_posix(),'sha256':digest(manifest_bytes),
                                    'artifact_count':len(manifest['artifacts']),
                                    'derived_artifact_count':sum(row['is_derived'] for row in manifest['artifacts'].values()),
                                    'source_and_public_bytes_distinguished':True,'self_excluded':True}}


class SelfTests(unittest.TestCase):
    def test_frozen_generation_fixture_is_published_with_its_test(self):
        code,_=selected_files()
        self.assertIn(Path('tests/test_generation_boundaries.py'),code)
        self.assertIn(Path('tests/fixtures/generation_real_raw_prose.json'),code)

    def test_recursive_and_encoded_fields(self):
        original={'thinking':'private','api_key':'private','thinking_char_count':7,
                  'file':'/Users/name/private/db.sqlite','nested':{'think':'private'},
                  'raw_content':json.dumps({'thinking':'private','summary':'Visible','file':'/tmp/private/cache'})}
        result=public_safe(original)
        self.assertEqual(original['thinking'],'private')
        self.assertNotIn('thinking',result)
        self.assertNotIn('api_key',result)
        self.assertEqual(result['thinking_char_count'],7)
        self.assertEqual(json.loads(result['raw_content']),{'summary':'Visible','file':'[local path]'})
        self.assertGreater(check_public(result),0)
    def test_safe_encoded_model_json_preserves_exact_original_bytes_and_hash(self):
        original='{\n  "summary" : "\\u4f9b\\u5e94 40\\u202fmcm/d",\n  "references" : [ { "id" : "N0" } ]\n}\n'
        result=public_safe({'raw_content':original,'response':{'message':{'content':original}}})
        self.assertEqual(result['raw_content'],original)
        self.assertEqual(result['response']['message']['content'],original)
        self.assertEqual(digest(result['raw_content'].encode()),digest(original.encode()))
        self.assertGreater(check_public(result),0)
    def test_encoded_model_json_is_still_redacted_when_private_fields_exist(self):
        original='{ "summary" : "Visible", "thinking" : "private", "file" : "/Users/name/private/news" }'
        clean=public_safe(original)
        self.assertNotEqual(clean,original)
        self.assertEqual(json.loads(clean),{'summary':'Visible','file':'[local path]'})
        self.assertGreater(check_public(clean),0)
        with self.assertRaises(ValueError):check_public(original)
    def test_encoded_json_keeps_reasoning_configuration_under_public_alias(self):
        original='{ "think" : "medium", "summary" : "Visible" }'
        clean=public_safe(original)
        self.assertNotEqual(clean,original)
        self.assertEqual(json.loads(clean),{'request_reasoning':'medium','summary':'Visible'})
        self.assertGreater(check_public(clean),0)
    def test_encoded_json_requires_both_recursive_and_original_text_identity(self):
        original='{ "summary" : "Visible" }'
        module=sys.modules[__name__]
        with patch.object(module,'redact_text',side_effect=lambda value:'changed original' if value==original else value):
            clean=public_safe(original)
        self.assertNotEqual(clean,original)
        self.assertEqual(json.loads(clean),{'summary':'Visible'})
        self.assertGreater(check_public(clean),0)
    def test_exact_reasoning_configuration_is_preserved_without_mutation(self):
        for level in ('low','medium','high'):
            with self.subTest(level=level):
                original={'think':level,'thinking':'private prose','thinking_char_count':17}
                clean=public_safe(original)
                self.assertEqual(clean,{'request_reasoning':level,'thinking_char_count':17})
                self.assertEqual(original,{'think':level,'thinking':'private prose','thinking_char_count':17})
                self.assertGreater(check_public(clean),0)
    def test_non_configuration_think_values_are_removed(self):
        for value in ('private prose','HIGH',' medium','medium ',True,0,None,{'mode':'high'},['high']):
            with self.subTest(value=value):
                self.assertEqual(public_safe({'think':value,'visible':1}),{'visible':1})
        self.assertEqual(public_safe({'Think':'high','thinking':'low','raw_think':'medium'}),{})
    def test_nested_and_encoded_reasoning_configuration(self):
        source={'calls':[{'think':'medium','thinking':'private'}],
                'raw_content':json.dumps({'think':'high','thinking':'private','raw_thinking':'private'})}
        clean=public_safe(source)
        self.assertEqual(clean['calls'],[{'request_reasoning':'medium'}])
        self.assertEqual(json.loads(clean['raw_content']),{'request_reasoning':'high'})
        self.assertGreater(check_public(clean),0)
    def test_reasoning_configuration_conflict_never_overwrites(self):
        self.assertEqual(public_safe({'think':'high','request_reasoning':'high'}),{'request_reasoning':'high'})
        for existing in ('medium',None,{'private':'value'}):
            with self.subTest(existing=existing):
                original={'think':'high','request_reasoning':existing}
                with self.assertRaisesRegex(ValueError,'configuration keys conflict'):
                    public_safe(original)
                self.assertEqual(original,{'think':'high','request_reasoning':existing})
    def test_credentials_in_text(self):
        clean=public_safe('Authorization: Bearer abcdefghijk123456 /home/name/.env api_key=abcdefghijklmnop')
        self.assertNotIn('abcdefghijk123456',clean)
        self.assertNotIn('/home/',clean)
        check_public(clean)
    def test_local_path_keys_are_sanitized(self):
        value=public_safe({'/Users/person/private/file':'hash'})
        self.assertEqual(value,{'[local path]':'hash'})
        check_public(value)
    def test_archives_and_runtime_are_forbidden(self):
        for value in ('reports/repair_2026-10-20/original_source.tar',
                      'reports/repair_2026-10-20/original_source/file.py','.runtime/file',
                      'reports/repair_2026-10-20/source_provenance_output_feasibility_v2_3/source_utf8_archive.json',
                      'data/backups/secret.json','../outside'):
            with self.assertRaises(ValueError):allowed_relative(Path(value))
    def test_broken_json_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'in-progress.json'
            path.write_text('{"partial":',encoding='utf-8')
            with self.assertRaises(ValueError):sanitize_report(path)
    def test_xml_paths_are_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'tests.xml'
            path.write_text('<testsuite><testcase file="/Users/name/test.py"/><system-out>/tmp/private/log</system-out></testsuite>',encoding='utf-8')
            clean,_=sanitize_report(path)
            self.assertNotIn(b'/Users/',clean)
            self.assertNotIn(b'/tmp/',clean)
            ET.fromstring(clean)
    def test_embedded_thinking_is_rejected_if_not_json_parseable(self):
        with self.assertRaises(ValueError):check_public('partial {"thinking": "private')
    def test_partial_private_key_is_rejected(self):
        with self.assertRaises(ValueError):check_public('-----BEGIN PRIVATE KEY-----\npartial')
    def test_allowlist_script_names_and_detail_stages(self):
        self.assertEqual(SCRIPTS,('prepare-github.py','validate-news-logic.py','repair-validation-report.py',
                                  'validate-deployment.py','sync-public-evidence.py','apply-analysis-reviews.py','scan-public-snapshot.py'))
        self.assertIn('candidate-production-validation-detail',REPORT.DETAIL_STAGES)
    def test_provenance_trial_nested_evidence_is_explicit_and_complete(self):
        _,reports=selected_files()
        self.assertEqual(len(PROVENANCE_EVIDENCE),24)
        self.assertEqual(len(set(PROVENANCE_EVIDENCE)),24)
        self.assertTrue(set(PROVENANCE_EVIDENCE).issubset(reports))
        for path in PROVENANCE_EVIDENCE:
            self.assertEqual(path.suffix,'.json')
            allowed_relative(path)
    def test_only_four_verified_ui_images_are_published_byte_exact(self):
        _,reports=selected_files()
        self.assertEqual(set(path for path in reports if path.suffix in ('.png','.jpg','.jpeg')),set(VERIFIED_UI_IMAGES))
        self.assertEqual(VERIFIED_UI_IMAGES,(EVIDENCE/'error_ui_final.png',EVIDENCE/'error_ui_v2.png',
                                            EVIDENCE/'error_ui_v2_2.jpg',EVIDENCE/'error_ui_v2_3.jpg'))
        for relative in VERIFIED_UI_IMAGES:
            with self.subTest(path=relative):
                clean,keys=sanitize_report(ROOT/relative)
                self.assertEqual(clean,(ROOT/relative).read_bytes())
                self.assertEqual(keys,0)
    def test_arbitrary_or_incomplete_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            for name,content in [('arbitrary.png',b'\x89PNG\r\n\x1a\nprivate'),('arbitrary.jpg',b'\xff\xd8\xffprivate\xff\xd9')]:
                path=Path(temporary)/name;path.write_bytes(content)
                with self.subTest(name=name),self.assertRaisesRegex(ValueError,'allowlist'):sanitize_report(path)
        for relative,content in [(VERIFIED_UI_IMAGES[0],b'not a complete PNG'),
                                 (VERIFIED_UI_IMAGES[2],b'\xff\xd8\xfftruncated'),
                                 (VERIFIED_UI_IMAGES[3],b'\xff\xd8\xfftruncated')]:
            with self.subTest(path=relative),patch.object(sys.modules[__name__],'read_stable',return_value=content):
                with self.assertRaisesRegex(ValueError,'complete PNG or JPEG image'):sanitize_report(ROOT/relative)
    def test_publication_manifest_distinguishes_source_and_derived_bytes(self):
        plans=[(Path('ttf_news/example.py'),b'print(1)\n',b'print(1)\n','source'),
               (EVIDENCE/'example.json',b'{"think":"medium"}',b'{"request_reasoning":"medium"}\n','evidence'),
               (EVIDENCE/'tests.xml',b'<testsuite/>',b'<?xml version="1.0"?><testsuite />','evidence'),
               (EVIDENCE/'example.log',b'/Users/private/log',b'[local path]','evidence')]
        manifest=build_publication_manifest(plans)
        validate_publication_manifest(manifest,plans)
        check_public(manifest)
        self.assertNotIn(PUBLICATION_MANIFEST.as_posix(),manifest['artifacts'])
        source=manifest['artifacts']['ttf_news/example.py']
        self.assertEqual(source['source_bytes_sha256'],source['public_bytes_sha256'])
        self.assertFalse(source['is_derived'])
        entry=manifest['artifacts'][(EVIDENCE/'example.json').as_posix()]
        self.assertNotEqual(entry['source_bytes_sha256'],entry['public_bytes_sha256'])
        self.assertTrue(entry['is_derived'])
        self.assertTrue(entry['evidence_flags']['sanitization_applied'])
        self.assertFalse(entry['evidence_flags']['original_bytes_preserved'])
    def test_publication_manifest_rejects_missing_stale_changed_and_duplicate_maps(self):
        relative=EVIDENCE/'example.json';plans=[(relative,b'original',b'public','evidence')]
        manifest=build_publication_manifest(plans)
        missing=json.loads(json.dumps(manifest));missing['artifacts'].clear()
        with self.assertRaisesRegex(ValueError,'Missing'):validate_publication_manifest(missing,plans)
        for field in ('source_bytes_sha256','public_bytes_sha256','transformation','is_derived'):
            with self.subTest(field=field):
                stale=json.loads(json.dumps(manifest));stale['artifacts'][relative.as_posix()][field]='changed'
                with self.assertRaisesRegex(ValueError,'Stale or changed'):validate_publication_manifest(stale,plans)
        with self.assertRaisesRegex(ValueError,'Stale or changed'):
            validate_publication_manifest(manifest,[(relative,b'changed source',b'public','evidence')])
        with self.assertRaisesRegex(ValueError,'Stale or changed'):
            validate_publication_manifest(manifest,[(relative,b'original',b'changed public','evidence')])
        with self.assertRaisesRegex(ValueError,'Duplicate'):build_publication_manifest(plans+plans)
    def test_publication_manifest_excludes_self_and_paths_outside_allowlist(self):
        for relative in (PUBLICATION_MANIFEST,Path('../escape'),Path('/tmp/secret'),Path('.runtime/private')):
            with self.subTest(path=relative),self.assertRaises(ValueError):
                build_publication_manifest([(relative,b'original',b'public','evidence')])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run',action='store_true',help='Validate all inputs and sanitization without copying')
    parser.add_argument('--self-test',action='store_true',help='Only run synthetic temporary-file tests')
    args=parser.parse_args()
    if args.self_test:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SelfTests))
        return 0 if result.wasSuccessful() else 1
    try:
        result=sync(dry_run=args.dry_run)
    except (OSError,ValueError,ET.ParseError) as exc:
        # A failed/in-progress JSON is an explicit failure, never an omission.
        print(json.dumps({'status':'failed','error':redact_text(str(exc))},ensure_ascii=False),file=sys.stderr)
        return 1
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
