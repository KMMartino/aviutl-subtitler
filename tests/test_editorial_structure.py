import unittest

from subtitler.editorial_structure import activity_packets, activity_structure, compile_selections, support_rejections, validate_choices
from subtitler.errors import SubtitlerError
from subtitler.semantic_utterances import semantic_cut_rejections


class StructuredEditingTests(unittest.TestCase):
    def test_states_cover_source_and_keep_canonical_parentage(self):
        source = {'source_id':'s','duration_ms':10000,'stages':{'semantic_spans':{'output':{
            'event_graph':{'nodes':[{'event_id':'e','start_ms':1000,'end_ms':8000,'observed_label':'Shop'}]},
            'activity_episodes':[{'episode_id':'a','level':1,'label':'Prepare','start_ms':1000,'end_ms':9000}]}}}}
        result=activity_structure(source)
        self.assertEqual([(s['start_ms'],s['end_ms']) for s in result['states']],[(0,1000),(1000,8000),(8000,9000),(9000,10000)])
        self.assertEqual(result['states'][1]['activity_id'],'a')
        self.assertTrue(result['states'][2]['uncertain'])

    def test_retained_meaning_preserves_dependency_and_demonstration(self):
        states=[{'state_id':str(i),'activity_id':'a','start_ms':i*10000,'end_ms':(i+1)*10000,'uncertain':False} for i in range(3)]
        units=[{'unit_id':'plan','start_ms':1000,'end_ms':3000,'dependency_unit_ids':[]},
               {'unit_id':'reference','start_ms':21000,'end_ms':23000,'dependency_unit_ids':['plan']}]
        choices={'states':[{'state_id':str(i),'decision':'omit','contribution':'Routine surplus'} for i in range(3)],
                 'utterances':[{'unit_id':'plan','decision':'omit','supporting_state_ids':['1']},
                               {'unit_id':'reference','decision':'keep','supporting_state_ids':[]}], 'trim_pause_ids':[]}
        cuts,restored=compile_selections({'states':states},units,[choices],[{'id':'f','start_ms':0,'end_ms':30000}],30000)
        self.assertTrue(restored)
        self.assertFalse(any(c['start_ms']<3000 and c['end_ms']>1000 for c in cuts))
        self.assertFalse(any(c['start_ms']<20000 and c['end_ms']>10000 for c in cuts))

    def test_visual_boundary_cannot_split_crossing_thought(self):
        states=[{'state_id':'a','activity_id':'a','start_ms':0,'end_ms':5000,'uncertain':False},
                {'state_id':'b','activity_id':'b','start_ms':5000,'end_ms':10000,'uncertain':False}]
        units=[{'unit_id':'u','start_ms':4000,'end_ms':7000,'dependency_unit_ids':[]}]
        choices={'states':[{'state_id':'a','decision':'keep','contribution':'Setup'},
                           {'state_id':'b','decision':'omit','contribution':'Surplus'}],
                 'utterances':[{'unit_id':'u','decision':'omit','supporting_state_ids':[]}],'trim_pause_ids':[]}
        cuts,_=compile_selections({'states':states},units,[choices],[{'id':'f','start_ms':0,'end_ms':10000}],10000)
        self.assertEqual([(c['start_ms'],c['end_ms']) for c in cuts],[(8000,10000)])
        packet=activity_packets({'activities':[{'activity_id':'a','start_ms':0,'end_ms':300000},
                                               {'activity_id':'b','start_ms':300000,'end_ms':600000}]},
                                [{'start_ms':299000,'end_ms':302000}],600000)
        self.assertEqual(len(packet),1)

    def test_duplicate_choices_cannot_hide_omitted_state(self):
        with self.assertRaises(SubtitlerError):
            validate_choices({'states':[{'state_id':'a'},{'state_id':'a'}],'utterances':[]},
                             [{'state_id':'a'},{'state_id':'b'}],[])

    def test_padding_does_not_restore_independent_meaning_or_its_visual_support(self):
        states=[{'state_id':'a','activity_id':'a','start_ms':0,'end_ms':8000,'uncertain':False},
                {'state_id':'b','activity_id':'a','start_ms':8000,'end_ms':10000,'uncertain':False}]
        units=[{'unit_id':'a','start_ms':1000,'end_ms':2000,'dependency_unit_ids':[]},
               {'unit_id':'b','start_ms':2500,'end_ms':3500,'dependency_unit_ids':[]}]
        choices={'states':[{'state_id':s['state_id'],'decision':'omit','contribution':'Surplus'} for s in states],
                 'utterances':[{'unit_id':'a','decision':'keep','supporting_state_ids':[]},
                               {'unit_id':'b','decision':'omit','supporting_state_ids':['b']}],'trim_pause_ids':[]}
        cuts,_=compile_selections({'states':states},units,[choices],[{'id':'f','start_ms':0,'end_ms':10000}],10000)
        self.assertTrue(any(c['end_ms'] == 10000 and c['start_ms'] <= 2500 for c in cuts))
        # A later review can restore speech that was initially omitted.
        remaining = [{'cut_id':'support','start_ms':8000,'end_ms':10000}]
        self.assertIn('support', support_rejections(remaining,units,{'states':states},[choices]))

    def test_contiguous_aligned_meanings_can_be_omitted_without_padding_cascade(self):
        states=[{'state_id':'s','activity_id':'a','start_ms':0,'end_ms':12000,'uncertain':False}]
        units=[{'unit_id':name,'start_ms':start,'end_ms':end,'dependency_unit_ids':[], 'alignment':'word'}
               for name,start,end in [('a',1000,4000),('b',4000,7000),('c',7000,10000)]]
        choices={'states':[{'state_id':'s','decision':'omit','contribution':'Surplus'}],
                 'utterances':[{'unit_id':u['unit_id'],'decision':'omit' if u['unit_id']=='b' else 'keep',
                                'supporting_state_ids':[]} for u in units],'trim_pause_ids':[]}
        artifact={'units':units,'duration_ms':12000,'raw_vad_available':True,
                  'voice_activity':[{'start_ms':3000,'end_ms':8000}], 'tokens':[]}
        cuts,_=compile_selections({'states':states},units,[choices],[],12000,semantic_artifact=artifact)
        middle=next(c for c in cuts if c['start_ms']==4000)
        self.assertEqual(middle['end_ms'],7000)
        self.assertTrue(all(e['review_required'] and e['voice_evidence']=='active' for e in middle['boundary_provenance']))
        # Voice activity flags uncertainty; it neither invents a word boundary
        # nor prohibits removing an entire independently selected meaning.
        artifact['raw_vad_available']=False
        self.assertEqual(semantic_cut_rejections([{'cut_id':'middle',**middle}],artifact),{})
        units[2]['dependency_unit_ids']=['b']
        self.assertIn('middle',semantic_cut_rejections([{'cut_id':'middle',**middle}],artifact))
        dependent,restored=compile_selections({'states':states},units,[choices],[],12000)
        self.assertTrue(restored)
        self.assertFalse(any(c['start_ms']<7000 and c['end_ms']>4000 for c in dependent))

    def test_actual_overlapping_extents_remain_inseparable(self):
        states=[{'state_id':'s','activity_id':'a','start_ms':0,'end_ms':10000,'uncertain':False}]
        units=[{'unit_id':'a','start_ms':1000,'end_ms':4000,'dependency_unit_ids':[]},
               {'unit_id':'b','start_ms':3900,'end_ms':5000,'dependency_unit_ids':[]},
               {'unit_id':'c','start_ms':4900,'end_ms':7000,'dependency_unit_ids':[]}]
        choices={'states':[{'state_id':'s','decision':'omit','contribution':'Surplus'}],
                 'utterances':[{'unit_id':u['unit_id'],'decision':'keep' if u['unit_id']=='a' else 'omit',
                                'supporting_state_ids':[]} for u in units],'trim_pause_ids':[]}
        cuts,_=compile_selections({'states':states},units,[choices],[],10000)
        self.assertFalse(any(c['start_ms']<7000 and c['end_ms']>1000 for c in cuts))
