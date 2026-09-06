package com.vitranslate.pdf

import com.vitranslate.pdf.repository.GoogleTranslateEngine
import java.io.IOException
import kotlin.test.Test
import kotlin.test.assertFailsWith

class GoogleTranslateEngineTest {
    @Test
    fun overlongSegmentIsRejectedInsteadOfSilentlyTruncated() {
        val engine = GoogleTranslateEngine()

        assertFailsWith<IOException> {
            engine.translate("a".repeat(GoogleTranslateEngine.MAXIMUM_SEGMENT_CHARACTERS + 1))
        }
    }
}
