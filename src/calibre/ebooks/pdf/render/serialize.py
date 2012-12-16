#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:fdm=marker:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__copyright__ = '2012, Kovid Goyal <kovid at kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import hashlib
from future_builtins import map
from collections import namedtuple

from calibre.constants import (__appname__, __version__)
from calibre.ebooks.pdf.render.common import (
    Reference, EOL, serialize, Stream, Dictionary, String, Name, Array)
from calibre.ebooks.pdf.render.fonts import FontManager

PDFVER = b'%PDF-1.6'

Color = namedtuple('Color', 'red green blue opacity')

class IndirectObjects(object):

    def __init__(self):
        self._list = []
        self._map = {}
        self._offsets = []

    def __len__(self):
        return len(self._list)

    def add(self, o):
        self._list.append(o)
        ref = Reference(len(self._list), o)
        self._map[id(o)] = ref
        self._offsets.append(None)
        return ref

    def commit(self, ref, stream):
        self.write_obj(stream, ref.num, ref.obj)

    def write_obj(self, stream, num, obj):
        stream.write(EOL)
        self._offsets[num-1] = stream.tell()
        stream.write('%d 0 obj'%num)
        stream.write(EOL)
        serialize(obj, stream)
        if stream.last_char != EOL:
            stream.write(EOL)
        stream.write('endobj')
        stream.write(EOL)

    def __getitem__(self, o):
        try:
            return self._map[id(self._list[o] if isinstance(o, int) else o)]
        except (KeyError, IndexError):
            raise KeyError('The object %r was not found'%o)

    def pdf_serialize(self, stream):
        for i, obj in enumerate(self._list):
            offset = self._offsets[i]
            if offset is None:
                self.write_obj(stream, i+1, obj)

    def write_xref(self, stream):
        self.xref_offset = stream.tell()
        stream.write(b'xref'+EOL)
        stream.write('0 %d'%(1+len(self._offsets)))
        stream.write(EOL)
        stream.write('%010d 65535 f '%0)
        stream.write(EOL)

        for offset in self._offsets:
            line = '%010d 00000 n '%offset
            stream.write(line.encode('ascii') + EOL)
        return self.xref_offset

class Page(Stream):

    def __init__(self, parentref, *args, **kwargs):
        super(Page, self).__init__(*args, **kwargs)
        self.page_dict = Dictionary({
            'Type': Name('Page'),
            'Parent': parentref,
        })
        self.opacities = {}
        self.fonts = {}

    def set_opacity(self, opref):
        if opref not in self.opacities:
            self.opacities[opref] = 'Opa%d'%len(self.opacities)
        name = self.opacities[opref]
        serialize(Name(name), self)
        self.write(b' gs ')

    def add_font(self, fontref):
        if fontref not in self.fonts:
            self.fonts[fontref] = 'F%d'%len(self.fonts)
        return self.fonts[fontref]

    def add_resources(self):
        r = Dictionary()
        if self.opacities:
            extgs = Dictionary()
            for opref, name in self.opacities.iteritems():
                extgs[name] = opref
            r['ExtGState'] = extgs
        if self.fonts:
            fonts = Dictionary()
            for ref, name in self.fonts.iteritems():
                fonts[name] = ref
            r['Font'] = fonts
        if r:
            self.page_dict['Resources'] = r

    def end(self, objects, stream):
        contents = objects.add(self)
        objects.commit(contents, stream)
        self.page_dict['Contents'] = contents
        self.add_resources()
        ret = objects.add(self.page_dict)
        objects.commit(ret, stream)
        return ret

class Path(object):

    def __init__(self):
        self.ops = []

    def move_to(self, x, y):
        self.ops.append((x, y, 'm'))

    def line_to(self, x, y):
        self.ops.append((x, y, 'l'))

    def curve_to(self, x1, y1, x2, y2, x, y):
        self.ops.append((x1, y1, x2, y2, x, y, 'c'))

class Text(object):

    def __init__(self):
        self.transform = self.default_transform = [1, 0, 0, 1, 0, 0]
        self.font_name = 'Times-Roman'
        self.font_path = None
        self.horizontal_scale = self.default_horizontal_scale = 100
        self.word_spacing = self.default_word_spacing = 0
        self.char_space = self.default_char_space = 0
        self.size = 12
        self.text = ''

    def set_transform(self, *args):
        if len(args) == 1:
            m = args[0]
            vals = [m.m11(), m.m12(), m.m21(), m.m22(), m.dx(), m.dy()]
        else:
            vals = args
        self.transform = vals

    def pdf_serialize(self, stream, font_name):
        if not self.text: return
        stream.write_line('BT ')
        serialize(Name(font_name), stream)
        stream.write(' %g Tf '%self.size)
        stream.write(' '.join(map(type(u''), self.transform)) + ' Tm ')
        if self.horizontal_scale != self.default_horizontal_scale:
            stream.write('%g Tz '%self.horizontal_scale)
        if self.word_spacing != self.default_word_spacing:
            stream.write('%g Tw '%self.word_spacing)
        if self.char_space != self.default_char_space:
            stream.write('%g Tc '%self.char_space)
        stream.write_line()
        serialize(String(self.text), stream)
        stream.write(' Tj ')
        stream.write_line('ET')


class Catalog(Dictionary):

    def __init__(self, pagetree):
        super(Catalog, self).__init__({'Type':Name('Catalog'),
            'Pages': pagetree})

class PageTree(Dictionary):

    def __init__(self, page_size):
        super(PageTree, self).__init__({'Type':Name('Pages'),
            'MediaBox':Array([0, 0, page_size[0], page_size[1]]),
            'Kids':Array(), 'Count':0,
        })

    def add_page(self, pageref):
        self['Kids'].append(pageref)
        self['Count'] += 1

class HashingStream(object):

    def __init__(self, f):
        self.f = f
        self.tell = f.tell
        self.hashobj = hashlib.sha256()
        self.last_char = b''

    def write(self, raw):
        raw = raw if isinstance(raw, bytes) else raw.encode('ascii')
        self.f.write(raw)
        self.hashobj.update(raw)
        if raw:
            self.last_char = raw[-1]

class PDFStream(object):

    PATH_OPS = {
        # stroke fill   fill-rule
        ( False, False, 'winding')  : 'n',
        ( False, False, 'evenodd')  : 'n',
        ( False, True,  'winding')  : 'f',
        ( False, True,  'evenodd')  : 'f*',
        ( True,  False, 'winding')  : 'S',
        ( True,  False, 'evenodd')  : 'S',
        ( True,  True,  'winding')  : 'B',
        ( True,  True,  'evenodd')  : 'B*',
    }

    def __init__(self, stream, page_size, compress=False):
        self.stream = HashingStream(stream)
        self.compress = compress
        self.write_line(PDFVER)
        self.write_line(b'%íì¦"')
        creator = ('%s %s [http://calibre-ebook.com]'%(__appname__,
                                    __version__))
        self.write_line('%% Created by %s'%creator)
        self.objects = IndirectObjects()
        self.objects.add(PageTree(page_size))
        self.objects.add(Catalog(self.page_tree))
        self.current_page = Page(self.page_tree, compress=self.compress)
        self.info = Dictionary({'Creator':String(creator),
                                'Producer':String(creator)})
        self.stroke_opacities, self.fill_opacities = {}, {}
        self.font_manager = FontManager(self.objects)

    @property
    def page_tree(self):
        return self.objects[0]

    @property
    def catalog(self):
        return self.objects[1]

    def write_line(self, byts=b''):
        byts = byts if isinstance(byts, bytes) else byts.encode('ascii')
        self.stream.write(byts + EOL)

    def transform(self, *args):
        if len(args) == 1:
            m = args[0]
            vals = [m.m11(), m.m12(), m.m21(), m.m22(), m.dx(), m.dy()]
        else:
            vals = args
        cm = ' '.join(map(type(u''), vals))
        self.current_page.write_line(cm + ' cm')

    def set_rgb_colorspace(self):
        self.current_page.write_line('/DeviceRGB CS /DeviceRGB cs')

    def save_stack(self):
        self.current_page.write_line('q')

    def restore_stack(self):
        self.current_page.write_line('Q')

    def reset_stack(self):
        self.current_page.write_line('Q q')

    def draw_rect(self, x, y, width, height, stroke=True, fill=False):
        self.current_page.write('%g %g %g %g re '%(x, y, width, height))
        self.current_page.write_line(self.PATH_OPS[(stroke, fill, 'winding')])

    def write_path(self, path):
        for i, op in enumerate(path.ops):
            if i != 0:
                self.current_page.write_line()
            for x in op:
                self.current_page.write(type(u'')(x) + ' ')

    def draw_path(self, path, stroke=True, fill=False, fill_rule='winding'):
        if not path.ops: return
        self.write_path(path)
        self.current_page.write_line(self.PATH_OPS[(stroke, fill, fill_rule)])

    def add_clip(self, path, fill_rule='winding'):
        if not path.ops: return
        self.write_path(path)
        op = 'W' if fill_rule == 'winding' else 'W*'
        self.current_page.write_line(op + ' ' + 'n')

    def set_dash(self, array, phase=0):
        array = Array(array)
        serialize(array, self.current_page)
        self.current_page.write(b' ')
        serialize(phase, self.current_page)
        self.current_page.write_line(' d')

    def set_line_width(self, width):
        serialize(width, self.current_page)
        self.current_page.write_line(' w')

    def set_line_cap(self, style):
        serialize({'flat':0, 'round':1, 'square':2}.get(style),
                  self.current_page)
        self.current_page.write_line(' J')

    def set_line_join(self, style):
        serialize({'miter':0, 'round':1, 'bevel':2}[style], self.current_page)
        self.current_page.write_line(' j')

    def set_stroke_color(self, color):
        opacity = color.opacity
        if opacity not in self.stroke_opacities:
            op = Dictionary({'Type':Name('ExtGState'), 'CA': opacity})
            self.stroke_opacities[opacity] = self.objects.add(op)
        self.current_page.set_opacity(self.stroke_opacities[opacity])
        self.current_page.write_line(' '.join(map(type(u''), color[:3])) + ' SC')

    def set_fill_color(self, color):
        opacity = color.opacity
        if opacity not in self.fill_opacities:
            op = Dictionary({'Type':Name('ExtGState'), 'ca': opacity})
            self.fill_opacities[opacity] = self.objects.add(op)
        self.current_page.set_opacity(self.fill_opacities[opacity])
        self.current_page.write_line(' '.join(map(type(u''), color[:3])) + ' sc')

    def end_page(self):
        pageref = self.current_page.end(self.objects, self.stream)
        self.page_tree.obj.add_page(pageref)
        self.current_page = Page(self.page_tree, compress=self.compress)

    def draw_text(self, text_object):
        if text_object.font_path is None:
            fontref = self.font_manager.add_standard_font(text_object.font_name)
        else:
            raise NotImplementedError()
        name = self.current_page.add_font(fontref)
        text_object.pdf_serialize(self.current_page, name)

    def end(self):
        if self.current_page.getvalue():
            self.end_page()
        inforef = self.objects.add(self.info)
        self.objects.pdf_serialize(self.stream)
        self.write_line()
        startxref = self.objects.write_xref(self.stream)
        file_id = String(self.stream.hashobj.hexdigest().decode('ascii'))
        self.write_line('trailer')
        trailer = Dictionary({'Root':self.catalog, 'Size':len(self.objects)+1,
                              'ID':Array([file_id, file_id]), 'Info':inforef})
        serialize(trailer, self.stream)
        self.write_line('startxref')
        self.write_line('%d'%startxref)
        self.stream.write('%%EOF')

