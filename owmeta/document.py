from six.moves.urllib.parse import urlparse, urlencode
from six.moves.urllib.request import Request, urlopen
from six.moves.urllib.error import HTTPError, URLError
import re
import logging

from owmeta_core.graph_object import IdentifierMissingException
from owmeta_core.context import Context
from owmeta_core.dataobject import DataObject, DatatypeProperty, Alias
from owmeta_core.mapper import mapped

from . import SCI_CTX
from . import bibtex as BIB

logger = logging.getLogger(__name__)


class WormbaseRetrievalException(Exception):
    pass


class PubmedRetrievalException(Exception):
    pass


# A little bit about why this a separate type from Document:
#
# This type corresponds to a document which has some statements that we care
# about. The key reason this is distinct from Document is that a document need
# not provide evidence of anything. For example, the `WormData.n4` file
# generated by insert_worm.py is a document, but it doesn't provide any
# scientific or logical justification for any of the statements made within it.
@mapped
class BaseDocument(DataObject):
    class_context = SCI_CTX

    def make_context_identifier(self):
        return self.make_identifier(self.identifier)

    @property
    def as_context(self):
        if self.context is not None:
            return Context.contextualize(self.context)(ident=self.make_context_identifier())
        else:
            return Context(ident=self.make_context_identifier())


@mapped
class Document(BaseDocument):
    """
    A representation of some document.

    Possible keys include::

        pmid, pubmed: a pubmed id or url (e.g., 24098140)
        wbid, wormbase: a wormbase id or url (e.g., WBPaper00044287)
        doi: a Digitial Object id or url (e.g., s00454-010-9273-0)
        uri: a URI specific to the document, preferably usable for accessing
             the document
    """

    class_context = SCI_CTX

    author = DatatypeProperty(multiple=True)
    ''' An author of the document '''

    doi = DatatypeProperty()
    ''' A Digital Object Identifier (DOI), optional '''

    uri = DatatypeProperty(multiple=True)
    ''' A non-standard URI for the document '''

    wbid = DatatypeProperty()
    ''' An ID from WormBase.org that points to a record, optional '''

    wormbaseid = Alias(wbid)
    ''' An alias to `wbid` '''

    pmid = DatatypeProperty()
    ''' A PubMed ID (PMID) that points to a paper '''

    year = DatatypeProperty()
    ''' The year (e.g., publication year) of the document '''

    date = Alias(year)
    ''' Alias to year '''

    title = DatatypeProperty()
    ''' The title of the document '''

    def __init__(
            self,
            bibtex=None,
            doi=None,
            pubmed=None,
            wormbase=None,
            **kwargs):
        """
        Parameters
        ----------
        bibtex : string
            A string containing a single BibTeX entry. Parsed during initialization, but not saved thereafter. optional
        doi : string
            A Digital Object Identifier (DOI). optional
        pubmed : string
            A PubMed ID (PMID) or URL that points to a paper. Ignored if 'pmid' is provided. optional
        wormbase : string
            An ID or URL from WormBase that points to a record. Ignored if `wbid` or `wormbaseid` are provided. optional
        """
        super(Document, self).__init__(**kwargs)

        self.id_precedence = ('doi', 'pmid', 'wbid', 'uri')

        if bibtex is not None:
            self.update_with_bibtex(bibtex)

        if pubmed is not None and not self.pmid.has_defined_value():
            if pubmed[:4] == 'http':
                _tmp = _pubmed_uri_to_pmid(pubmed)
                if _tmp is None:
                    raise ValueError("Couldn't convert Pubmed URL to a PubMed ID")
                pmid = _tmp
            else:
                pmid = pubmed
            self.pmid.set(pmid)

        if wormbase is not None and not self.wbid.has_defined_value():
            if wormbase[:4] == 'http':
                _tmp = _wormbase_uri_to_wbid(wormbase)
                if _tmp is None:
                    raise ValueError("Couldn't convert Wormbase URL to a Wormbase ID")
                wbid = _tmp
            else:
                wbid = wormbase
            self.wbid.set(wbid)

        if doi is not None:
            if doi[:4] == 'http':
                _tmp = _doi_uri_to_doi(doi)
                if _tmp is not None:
                    doi = _tmp
            self.doi.set(doi)

    def update_with_bibtex(self, bibtex):
        bib_db = BIB.loads(bibtex)
        if len(bib_db.entries) > 1:
            raise ValueError('The given BibTex string has %d entries.'
                             ' Cannot determine which entry to use for the document' % len(bib_db))
        BIB.update_document_with_bibtex(self, bib_db.entries[0])

    def defined_augment(self):
        for x in self.id_precedence:
            if getattr(self, x).has_defined_value():
                return True
        return False

    def identifier_augment(self):
        for idKind in self.id_precedence:
            idprop = getattr(self, idKind)
            if idprop.has_defined_value():
                s = str(idKind) + ":" + idprop.defined_values[0].identifier.n3()
                return self.make_identifier(s)
        raise IdentifierMissingException(self)

    # TODO: Provide a way to override modification of already set values.
    def update_from_wormbase(self, replace_existing=False):
        """ Queries wormbase for additional data to fill in the Document.

        If replace_existing is set to `True`, then existing values will be cleared.
        """

        # XXX: wormbase's REST API is pretty sparse in terms of data provided.
        #     Would be better off using AQL or the perl interface
        # _Very_ few of these have these fields filled in
        wbid = self.wbid.defined_values
        if len(wbid) == 1:
            wbid = wbid[0].identifier.toPython()

            # get the author
            try:
                root = self.conf.get('wormbase_api_root_url', 'http://rest.wormbase.org')
                url = root + '/rest/widget/paper/' + str(wbid) + '/overview?content-type=application%2Fjson'
                j = _json_request(url)
                if 'fields' in j:
                    f = j['fields']
                    if 'authors' in f:
                        dat = f['authors']['data']
                        if dat is not None:
                            if replace_existing and self.author.has_defined_value:
                                self.author.clear()
                            for x in dat:
                                self.author.set(x['label'])

                    for fname in ('pmid', 'year', 'title', 'doi'):
                        if fname in f and f[fname]['data'] is not None:
                            attr = getattr(self, fname)
                            if replace_existing and attr.has_defined_value:
                                attr.clear()
                            attr.set(f[fname]['data'])
            except Exception:
                logger.warning("Couldn't retrieve Wormbase data", exc_info=True)
        elif len(wbid) == 0:
            raise WormbaseRetrievalException("There is no Wormbase ID attached to this Document."
                                             " So no data can be retrieved")
        else:
            raise WormbaseRetrievalException("There is more than one Wormbase ID attached to this Document."
                                             " Please try with just one Wormbase ID")

    def _crossref_doi_extract(self):
        # Extract data from crossref
        def crRequest(doi):
            data = {'q': doi}
            data_encoded = urlencode(data)
            return _json_request(
                'http://search.labs.crossref.org/dois?%s' %
                data_encoded)

        doi = self.doi()
        if doi[:4] == 'http':
            doi = _doi_uri_to_doi(doi)
        try:
            r = crRequest(doi)
        except Exception:
            logger.warning("Couldn't retrieve Crossref info", exc_info=True)
            return
        # XXX: I don't think coins is meant to be used, but it has structured
        # data...
        if len(r) > 0:
            extra_data = r[0]['coins'].split('&amp;')
            fields = (x.split("=") for x in extra_data)
            fields = [[y.replace('+', ' ').strip() for y in x] for x in fields]
            authors = [x[1] for x in fields if x[0] == 'rft.au']
            for a in authors:
                self.author(a)
            # no error for bad ids, just an empty list
            if len(r) > 0:
                # Crossref can process multiple doi's at one go and return the
                # metadata. we just need the first one
                r = r[0]
                if 'title' in r:
                    self.title(r['title'])
                if 'year' in r:
                    self.year(r['year'])

    def update_from_pubmed(self):
        def pmRequest(pmid):
            import xml.etree.ElementTree as ET  # Python 2.5 and up
            url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=' + str(pmid)
            key = self.get('pubmed.api_key', None)
            if key:
                url += '&api_key=' + key
            else:
                logger.warning("PubMed API key not defined. API calls will be limited.")
            s = _url_request(url)
            if hasattr(s, 'charset'):
                parser = ET.XMLParser(encoding=s.charset)
            else:
                parser = None

            return ET.parse(s, parser)

        pmid = self.pmid.defined_values
        if len(pmid) == 1:
            pmid = pmid[0].identifier.toPython()
            try:
                tree = pmRequest(pmid)
            except Exception:
                logger.warning("Couldn't retrieve Pubmed info", exc_info=True)
                return
            for x in tree.findall('./DocSum/Item[@Name="AuthorList"]/Item'):
                self.author(x.text)

            for x in tree.findall('./DocSum/Item[@Name="Title"]'):
                self.title(x.text)

            for x in tree.findall('./DocSum/Item[@Name="DOI"]'):
                self.doi(x.text)

            for x in tree.findall('./DocSum/Item[@Name="PubDate"]'):
                self.year(x.text)

        elif len(pmid) == 0:
            raise PubmedRetrievalException('No Pubmed ID is attached to this document. Cannot retrieve Pubmed data')
        else:
            raise PubmedRetrievalException('More than one Pubmed ID is attached to this document.'
                                           ' Please try with just one Pubmed ID')


def _wormbase_uri_to_wbid(uri):
    return str(urlparse(uri).path.split("/")[2])


def _pubmed_uri_to_pmid(uri):
    return str(urlparse(uri).path.split("/")[2])


def _doi_uri_to_doi(uri):
    # DOI URL to DOI translation is complicated. This is a cop-out.
    parsed = urlparse(uri)
    if 'doi.org' in parsed.netloc:
        doi = parsed.path.split("/", 1)[1]
    else:
        doi = None

    return doi


class EmptyRes(object):
    def read(self):
        return bytes()


def _url_request(url, headers={}):
    try:
        r = Request(url, headers=headers)
        s = urlopen(r, timeout=1)
        info = dict(s.info())
        content_type = {k.lower(): info[k] for k in info}['content-type']
        md = re.search("charset *= *([^ ]+)", content_type)
        if md:
            s.charset = md.group(1)

        return s
    except HTTPError:
        logger.error("Error in request for {}".format(url), exc_info=True)
        return EmptyRes()
    except URLError:
        logger.error("Error in request for {}".format(url), exc_info=True)
        return EmptyRes()


def _json_request(url):
    import json
    headers = {'Accept': 'application/json'}
    try:
        data = _url_request(url, headers).read().decode('UTF-8')
        if hasattr(data, 'charset'):
            return json.loads(data, encoding=data.charset)
        else:
            return json.loads(data)
    except BaseException:
        logger.warning("Couldn't retrieve JSON data from " + url,
                       exc_info=True)
        return {}
