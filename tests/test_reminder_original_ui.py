import subprocess
import unittest
from tests.ui_page import core_source


class ReminderOriginalTests(unittest.TestCase):
    """A reminder unfolds into what the host said and, new in this change, the calendar text word for word."""
    def test_original_is_a_detail_and_makes_the_row_unfoldable(self):
        script = core_source() + r'''
const assert=require('node:assert/strict');
const e={kind:'reminder',title:'Call mum',text:'Time to call mum',original:'Call mum\n\nbefore six, ask about the trip',description:'',links:[]};
const d=RadioModel.entryDetails(e);
assert.equal(d.length,1);assert.equal(d[0].original,'Call mum\n\nbefore six, ask about the trip','verbatim, nothing cleaned');
assert.equal(d[0].segments[0].text,'Time to call mum','the spoken text stays');
assert.equal(RadioModel.expandable({kind:'reminder',title:'x',text:'',original:'only the original'}),true);
const old={kind:'reminder',title:'Спокен текст',links:[]};
assert.equal(RadioModel.entryDetails(old)[0].original,'');assert.equal(RadioModel.entryDetails(old)[0].segments[0].text,'Спокен текст','old entries unchanged');
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
