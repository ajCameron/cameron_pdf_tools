#!/usr/bin/env python

# Metadata can be embedded in PDF files in a number of different ways and placed.
# Older PDFs used "Info" in the XRefs trailer
# Newer ones use XMP metadata
# Some of them use both

# Wrapper for pdfminer - replacing the calibre method - which seems to rely on the existence of
# PDFTOHTML
# (should work on fixing that, so it'll be available with the old one)
# Using pdfminer which can be installed as a dependancy under python

# Done, for the moment

from __future__ import unicode_literals

import os
import subprocess
import shutil
import re
import uuid


from copy import deepcopy
from collections import defaultdict
from xml.etree import ElementTree as ET

from cameron_pdf_tools.constants import iswindows


PRODUCER_DROP_REGEX_SET = {r".*LaTeX.*", r".*Acrobat.*"}
INFO_DICT_KEY_DROP_SET = {
    r"the process that creates this pdf constitutes a trade secret of codemantra, llc and "
    r"is protected by the copyright laws of the united states"
}

from cameron_pdf_tools.python_tools import regex_dict_rekey
from cameron_pdf_tools.python_tools import regex_dict_str_rekey
from cameron_pdf_tools.python_tools import check_against_regex_set

from pdfminer.pdfparser import PDFParser
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdftypes import resolve1
from pdfminer.psparser import PSKeyword, PSLiteral
from pdfminer.utils import PDFDocEncoding



# Py2/Py3 compatibility layer
from six import iteritems
six_unicode = str

_ = lambda x: x


DEV_MODE = True


# An error raised when a resource is not found or the PDF file does something unexpected
class PdfParseError(Exception):
    def __init__(self, argument):
        self.argument = argument
        print(self.argument)

    def __str__(self):
        return repr(self.argument)


def get_metadata(stream):
    """
    Takes a path to a PDF file. Tries to parse it for metadata
    :param stream: The PDF file to be parsed
    :return MetaData object: A metadata object
    """
    print("In get_metadata")

    # https://stackoverflow.com/questions/14209214/reading-the-pdf-properties-metadata-in-python
    stream.seek(0)
    parser = PDFParser(stream)
    document = PDFDocument(parser)

    # The info metadata
    # document.info returns a list, with the first element being what appears to be the info dict
    # This provides some basic info about the dpcument (stuff an os needs?)
    info_dict = document.info[0]
    metadata_return = dict()
    metadata_return = process_metadata_info_dict(info_dict, metadata_return)

    # Finding the XMP data, if it exists, and processing it into dictionary form
    if "Metadata" in document.catalog:
        xmp_metadata = resolve1(document.catalog["Metadata"]).get_data()
        xmp_metadata_dict = xmp_to_dict(xmp_metadata)
        metadata_return = process_xmp_metadata_dict(xmp_metadata_dict, metadata_return)

    return metadata_return


def get_metadata_inplace(target_file):
    """
    Takes a path to a PDF file. Tries to parse it for metadata
    :param target_file: The PDF file to be parsed
    :return MetaData object: A metadata object
    """
    with open(target_file, "rb") as target_pdf_stream:
        return get_metadata(target_pdf_stream)





get_quick_metadata = get_metadata

########################################################################################################################


def read_info(outputdir, get_cover):
    """
    Read info dict and cover from a pdf file named src.pdf in outputdir.
    Note that this function changes the cwd to outputdir and is therefore not thread safe.
    Run it using fork_job. This is necessary as there is no safe way to pass unicode paths via command line arguments.
    This also ensures that if poppler crashes, no stale file handles are left for the original file, only for src.pdf.
    :param outputdir:
    :param get_cover:
    :return:
    """
    os.chdir(outputdir)
    pdfinfo = get_tool("pdfinfo")
    pdftoppm = get_tool("pdftoppm")
    ans = {}

    try:
        raw = subprocess.check_output([pdfinfo, "-meta", "-enc", "UTF-8", "src.pdf"])
    except subprocess.CalledProcessError as e:
        print("pdfinfo errored out with return code: %d" % e.returncode)
        return None

    # The XMP metadata could be in an encoding other than UTF-8, so split it out before trying to decode raw
    parts = re.split(br"^Metadata:", raw, 1, flags=re.MULTILINE)
    if len(parts) > 1:
        raw, ans["xmp_metadata"] = parts
    try:
        raw = raw.decode("utf-8")
    except UnicodeDecodeError:
        print("pdfinfo returned no UTF-8 data")
        return None

    for line in raw.splitlines():
        if ":" not in line:
            continue
        field, val = line.partition(":")[::2]
        val = val.strip()
        if field and val:
            ans[field] = val.strip()

    if get_cover:
        try:
            subprocess.check_call(
                [pdftoppm, "-singlefile", "-jpeg", "-cropbox", "src.pdf", "cover"]
            )
        except subprocess.CalledProcessError as e:
            print("pdftoppm errored out with return code: %d" % e.returncode)

    return ans


def page_images(pdfpath, outputdir, first=1, last=1):
    pdf_to_ppm = get_tool("pdftoppm")
    outputdir = os.path.abspath(outputdir)
    args = {}

    if iswindows:
        import win32process as w

        args["creationflags"] = w.HIGH_PRIORITY_CLASS | w.CREATE_NO_WINDOW

    try:
        subprocess.check_call(
            [
                pdf_to_ppm,
                "-cropbox",
                "-jpeg",
                "-f",
                str(first),
                "-l",
                str(last),
                pdfpath,
                os.path.join(outputdir, "page-images"),
            ],
            **args
        )
    except subprocess.CalledProcessError as e:
        raise ValueError("Failed to render PDF, pdftoppm errorcode: %s" % e.returncode)



########################################################################################################################


def process_metadata_info_dict(info_dict, md):
    """
    Takes a dictionary of metadata and a MetaData object. Tries to process one into the other.
    :param info_dict:
    :return:
    """
    info_dict = deepcopy(info_dict)
    regex_rekey_dict = {
        r"^Author$": "author",
        r"^.*CreationDate$": "timestamp",
        r"^.*Creator$": "creator",
        r"^ModDate$": "last_modified",
        r"^.*Producer$": "producer",
        r"^(ebx_)?Publisher$": "publisher",
        r"^Title$": "title",
    }

    for field in info_dict.keys():

        field_key = regex_dict_str_rekey(regex_rekey_dict, field.strip().lower())
        field_value = info_dict[field]

        # Check to see if the key is one of the known ignore keys - if it is then continue
        if field_key is None or field_value is None:
            continue
        if check_against_regex_set(INFO_DICT_KEY_DROP_SET, field_key):
            continue
        md, status = process_key_value_pair(
            field_key, field_value, info_dict.keys(), md
        )

        # If the status is False then the key value pair was not logged - try flipping the key and the value and
        # trying again - if this doesn't work then has to give up
        if not status:

            new_field_key = deepcopy(field_value)
            new_field_value = deepcopy(field_value)

            new_field_key = decode_text(new_field_key.name) if isinstance(new_field_key, PSLiteral) else new_field_key
            new_field_value = decode_text(new_field_value.name) if isinstance(new_field_value, PSLiteral) else new_field_value

            try:
                regex_key_check_status = check_against_regex_set(
                    INFO_DICT_KEY_DROP_SET, new_field_key
                )
            except TypeError:

                debug_msg = ["Cannot check against the INFO_DICT_KEY_DROP_SET - "
                             "the field value is not a string or buffer",
                             f"new_field_key - {new_field_key}",
                             f"type(new_field_value) - {type(new_field_value)}",
                             f"new_field_value - {new_field_value}",
                             f"type(new_field_value) - {type(new_field_value)}"]

                print("\n".join(debug_msg))

                continue

            if new_field_value is not None and regex_key_check_status:
                continue
            md, status = process_key_value_pair(
                new_field_key, new_field_value, info_dict.values(), md
            )

        if not status:
            debug_msg = ["Unexpected element found in info_dict in pdf:process_metadata_info_dict",
                         f"field_key - {field_key}",
                         f"field_value - {field_value}",
                         f"info_dict - {info_dict}"]
            print("\n".join(debug_msg))

    return md


def get_tool(tool_name):
    """
    Return the path to a tool's binary.
    :param tool_name:
    :return:
    """
    from cameron_pdf_tools.constants import PDFTOHTML

    base = os.path.dirname(PDFTOHTML)
    tool_path = os.path.join(base, tool_name)
    if os.path.exists(tool_path):
        return tool_path

    base_paths = ["/usr", "/usr/bin"]
    for base_path in base_paths:
        cand_tool_path = os.path.join(base_path, tool_name)
        if os.path.exists(cand_tool_path):
            return cand_tool_path

    # If the tool cannot be found then return None
    return None


def process_key_value_pair(key, value, info_dict_keys, md):
    """
    Process a key/value pair and add it to the given metadata object
    :param key:
    :param value:
    :param md:
    :type md: LiuXin or calibre md
    :return: md
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8")

    if key == "author":
        if "author" in md.keys():
            md["author"].append(value)
        else:
            md["author"] = [value, ]

    elif key == "creator":
        if "author" in md.keys():
            md["author"].append(value)
        else:
            md["author"] = [value, ]

    # Keywords are mapped into tags as well
    elif key == "keywords":

        keywords_str = value

        try:
            comma_in_keywords = True if "," in keywords_str else False
        except UnicodeDecodeError:
            # Cannot handle this
            comma_in_keywords = False

        if comma_in_keywords:
            tags = [kws for kws in keywords_str.split(",")]
        else:
            tags = [keywords_str, ]

        if "tags" in md:
            md["tags"].extend(tags)
        else:
            md["tags"] = tags

    elif key == "last_modified":
        md["last_modified"] = value

    # Another term for producer - should be used iff something more suitable is not present
    elif key == "llc":
        if "producer" not in info_dict_keys and not check_against_regex_set(
            PRODUCER_DROP_REGEX_SET, value
        ):
            if "producer" in md:
                md["producer"].append(value)
            else:
                md["producer"] = [value, ]

    elif key == "producer":
        if not check_against_regex_set(PRODUCER_DROP_REGEX_SET, value):

            if "producer" in md:
                md["producer"].append(value)
            else:
                md["producer"] = [value, ]

    elif key in ["publisher", "ebx_publisher"]:
        value = six_unicode(value)
        if value.startswith("/"):
            value = value[1:]
        if key != "publisher" and "publisher" not in info_dict_keys:
            if "publisher" in md:
                md["publisher"].append(value)
            else:
                md["publisher"] = [value, ]
        else:
            if "tags" in md:
                md["tags"].extend([value, ])
            else:
                md["tags"] = [value, ]

    elif key == "subject":
        if "tags" in md:
            md["tags"].extend([value, ])
        else:
            md["tags"] = [value, ]

    elif key == "timestamp":
        md["timestamp"] = value

    elif key == "title":
        md["title"] = value
        if "tags" in md:
            md["tags"].extend([value, ])
        else:
            md["tags"] = [value, ]

    # Not really knowing what universal is, mapping it to a tag (where everything I can't easily classified goes)
    elif key in [
        "universal",
    ]:
        # Ignore internal postscript tags - they are not helpful
        if isinstance(value, PSKeyword):
            value = six_unicode(value)
            if value.lower() == "pdf":
                pass
            else:
                err_str = f"unexpected value found when parsing universal tag - value - {value}"
                raise NotImplementedError(err_str)
        else:
            if "tags" in md:
                md["tags"].extend([value, ])
            else:
                md["tags"] = [value, ]

    elif key in ["universal pdf", "codemantra, llc", "pdfversion"]:
        pass

    else:
        return md, False

    return md, True


def process_xmp_metadata_dict(xmp_metadata_dict, metadata_return):
    """

    :param xmp_metadata_dict:
    :param metadata_return:
    :return:
    """
    assert isinstance(metadata_return, dict), "This should be a dict. No two ways about it."

    # XMP metadata has been parsed and returned in the form of a standardized dic.
    # see NS_MPA below for the various terms
    # Once parsed out of XML metadata is stored in, well, a dictionary of dictionary of dictionaries.
    # This is going to need some careful testing
    xmp_metadata_dict = deepcopy(xmp_metadata_dict)

    if "xapmm" in xmp_metadata_dict.keys():

        internal_identifier_dict = xmp_metadata_dict["xapmm"]

        for field in internal_identifier_dict.keys():

            if field == "InstanceID":
                pass
            elif field == "DocumentID":
                identifier = internal_identifier_dict[field]

                id_type_tokens = identifier.split(":")
                if len(id_type_tokens) == 1:
                    if DEV_MODE:
                        info_str = "Unrecognized type of identifier - "
                        info_str += repr(identifier)
                        raise PdfParseError(info_str)
                    else:
                        metadata_return["uuid"] = identifier
                elif len(id_type_tokens) == 2:
                    id_type = id_type_tokens[0]
                    if id_type == "uuid":
                        metadata_return["uuid"] = id_type_tokens[1]
                    else:
                        info_str = "Unrecognized type of identifier - "
                        info_str += repr(id_type)
                        info_str += repr(id_type_tokens)
                        raise PdfParseError(info_str)
                else:
                    info_str = "internal id tokens of an unexpected length - "
                    info_str += repr(id_type_tokens)
                    raise PdfParseError(info_str)
            else:
                info_str = "Unexpected key found in internal identifiers dictionary - "
                info_str += repr(field)
                raise PdfParseError(info_str)

    if "dc" in xmp_metadata_dict.keys():

        metadata_dict = xmp_metadata_dict["dc"]

        for field in metadata_dict.keys():

            value = metadata_dict[field]
            # If there is no value then just ignore it
            if not value:
                continue

            if field == "title":
                if isinstance(value, dict):
                    # An imperfect solution, but it'll do for the moment
                    if len(value) == 1:
                        metadata_return["title"] = [val for val in value][0]
                    else:
                        if DEV_MODE:
                            info_str = (
                                "Unexpected case found when trying to parse for the title - "
                                "{}".format(repr(value))
                            )
                            raise PdfParseError(info_str)
                        else:
                            metadata_return["title"] = [v for v in value][0]

                            if "tags" in metadata_return:
                                metadata_return["tags"].extend([v for v in value])
                            else:
                                metadata_return["tags"] = [v for v in value]


                else:
                    metadata_return["title"] = value

            elif field == "creator":
                # Assuming that any creator is an author
                # XMP standard doesn't seem to have a way to specify differently
                creator_dict = dict()
                creator_dict["authors"] = []
                if hasattr(value, "__iter__"):
                    metadata_return["author"] = ", ".join([v for v in value])
                else:
                    metadata_return["author"] = value

            elif field == "format":
                pass

            elif field == "publisher":
                if isinstance(value, list):
                    if len(value) == 1:
                        metadata_return.publisher = value
                    else:
                        if DEV_MODE:
                            info_str = (
                                "Unexpected case found when trying to parse the publisher - "
                                "{}".format(repr(value))
                            )
                            raise PdfParseError(info_str)
                        else:
                            metadata_return.publisher = value[0]

            elif field == "description":
                if value == {"x-default": None}:
                    # Ignore the default for podofo
                    continue
                else:
                    if DEV_MODE:
                        info_str = "Unexpected case found when trying to parse the publisher - {}".format(
                            value
                        )
                        raise PdfParseError(info_str)
                    else:
                        metadata_return["publisher"] = ", ".join([v for v in value.values()])

            elif field == "subject":
                # Assume that these are all tags - this is true, at least, for any PDFs with metadata updated by calibre
                if "tags" in metadata_return:
                    metadata_return["tags"].extend([t for t in value.values()])
                else:
                    metadata_return["tags"] = [t for t in value.values()]

            else:
                if DEV_MODE:
                    info_str = "Unrecongnized field encountered in dc dictionary - "
                    info_str += repr(field)
                    raise PdfParseError(info_str)

    return metadata_return



def decode_text(s):
    """
    Decodes a PDFDocEncoding string to Unicode.
    Adds py3 compatibility to pdfminer's version.
    """
    if type(s) == bytes and s.startswith(b'\xfe\xff'):
        return str(s[2:], 'utf-16be', 'ignore')
    else:
        ords = (ord(c) if type(c) == str else c for c in s)
        return ''.join(PDFDocEncoding[o] for o in ords)



class XmpParser(object):
    """
    By Matt Swain. Released under the MIT liscence.

    http://blog.matt-swain.com/post/25650072381/a-lightweight-xmp-parser-for-extracting-pdf
    Parses an XMP string into a dictionary.

    Usage:

        parser = XmpParser(xmpstring)
        meta = parser.meta
    """

    RDF_NS = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
    XML_NS = "{http://www.w3.org/XML/1998/namespace}"
    NS_MAP = {
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
        "http://purl.org/dc/elements/1.1/": "dc",
        "http://ns.adobe.com/xap/1.0/": "xap",
        "http://ns.adobe.com/pdf/1.3/": "pdf",
        "http://ns.adobe.com/xap/1.0/mm/": "xapmm",
        "http://ns.adobe.com/pdfx/1.3/": "pdfx",
        "http://prismstandard.org/namespaces/basic/2.0/": "prism",
        "http://crossref.org/crossmark/1.0/": "crossmark",
        "http://ns.adobe.com/xap/1.0/rights/": "rights",
        "http://www.w3.org/XML/1998/namespace": "xml",
    }

    def __init__(self, xmp):
        self.tree = ET.XML(xmp)
        self.rdftree = self.tree.find(self.RDF_NS + "RDF")

    @property
    def meta(self):
        """ A dictionary of all the parsed metadata. """
        meta = defaultdict(dict)
        for desc in self.rdftree.findall(self.RDF_NS + "Description"):
            try:
                desc_children = desc.getchildren()
            except AttributeError:
                return dict()
            for el in desc.getchildren():
                ns, tag = self._parse_tag(el)
                value = self._parse_value(el)
                meta[ns][tag] = value
        return dict(meta)

    def _parse_tag(self, el):
        """ Extract the namespace and tag from an element. """
        ns = None
        tag = el.tag
        if tag[0] == "{":
            ns, tag = tag[1:].split("}", 1)
            if ns in self.NS_MAP:
                ns = self.NS_MAP[ns]
        return ns, tag

    def _parse_value(self, el):
        """
        Extract the metadata value from an element.
        :param el: element to parse
        :return:
        """
        if el.find(self.RDF_NS + "Bag") is not None:
            value = []
            for li in el.findall(self.RDF_NS + "Bag/" + self.RDF_NS + "li"):
                value.append(li.text)
        elif el.find(self.RDF_NS + "Seq") is not None:
            value = []
            for li in el.findall(self.RDF_NS + "Seq/" + self.RDF_NS + "li"):
                value.append(li.text)
        elif el.find(self.RDF_NS + "Alt") is not None:
            value = {}
            for li in el.findall(self.RDF_NS + "Alt/" + self.RDF_NS + "li"):
                value[li.get(self.XML_NS + "lang")] = li.text
        else:
            value = el.text
        return value


def xmp_to_dict(xmp):
    """
    Shorthand function for parsing an XMP string into a python dictionary.
    :param xmp:
    :return:
    """
    return XmpParser(xmp).meta


if __name__ == "__main__":

    test_path = "/home/eric/pdf_test_file_1.pdf"

    assert os.path.exists(test_path) and os.path.isfile(test_path)

    get_metadata_inplace(test_path)





