import en from './en.json' with { type: 'json' };
import de from './de.json' with { type: 'json' };
import es from './es.json' with { type: 'json' };
import fr from './fr.json' with { type: 'json' };
import it from './it.json' with { type: 'json' };
import zhHans from './zh-Hans.json' with { type: 'json' };

export const FRONTEND_TRANSLATIONS = Object.freeze({ en, de, es, fr, it, 'zh-Hans': zhHans });
