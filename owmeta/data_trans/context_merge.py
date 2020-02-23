from owmeta_core.datasource import DataTranslator, OneOrMore
from owmeta_core.mapper import mapped

from .. import SCI_CTX

from .common_data import TRANS_NS
from .data_with_evidence_ds import DataWithEvidenceDataSource


@mapped
class ContextMergeDataTranslator(DataTranslator):
    class_context = SCI_CTX

    translator_identifier = TRANS_NS.ContextMergeDataTranslator
    input_type = OneOrMore(DataWithEvidenceDataSource)
    output_type = DataWithEvidenceDataSource

    def translate(self, *sources):
        if not sources:
            raise Exception("No sources were provided")

        sources = sorted(sources, key=lambda s: s.identifier)
        res = self.make_new_output(sources=sources)

        for src in sources:
            res.data_context.add_import(src.data_context)
            res.evidence_context.add_import(src.evidence_context)
        return res
